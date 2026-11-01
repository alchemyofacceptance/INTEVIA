"""C3 seed: ten collected tests, zero threads; admission evidence is pending.

Two pure vocabulary tests; eight PostgreSQL command tests. The corpus's 86
cases and the named subcases are disclosed in the ledger, not counted as one
unit of work for cost purposes. No archived test is imported or changed.

Fixture map: found_nonroot -> ACTIVE LO, member and Coordinator histories;
seed_test_gateway -> effective, CQ-off configuration; database_now -> fresh
coordinates after fixture commits; seed identities have no transition history,
so command tests assert stored receipts and do not claim replay-audience coverage.
The founding regression separately registers a real learner and calls the real
formation service with labelled fixture terms/principal. setUp invokes found_nonroot, which enforces the disposable-database check.
The imported C2 writers do not independently enforce that check.
T06 also uses guarded writers _add_member/_propose/_consent/_lo_transition from
tests/test_sr_office_guards.py; rely on setUp's found_nonroot for the disposable-
database check; never C2's definition bypass. CUSTOM setup uses the installed
beta-grant composer and a labelled policy record, with every guard active. This
is entitlement setup only, not a real opt-in or grant/lapse lineage witness.
Raw reads in this module: database clock -> aware datetime; complete snapshots
use ORM values_list (JSON decoded by Django); no raw-jsonb dict assumption.
"""
from dataclasses import asdict, replace
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4
import hashlib
import json

from django.db import DatabaseError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from core import models
from core.organism_contract import OrganismAction, qualification_planes
from core.organism_authority_facts import (
    S015_AUTHORITY_FACTS_SCHEMA, S015AuthorityFactsUndeclared,
    validate_authority_facts, role_event_form,
)
from src.intevia.services.organism_authority import (
    S015Authority, S015AuthorityDenied, S015AuthorityMalformed, S015PostFoundingAuthorityRefusal,
    S015PostFoundingAuthorityResponse,
    S015PostFoundingAuthorityRequest, S015PreFoundingAuthorityRequest,
    S015OwnApplicantAuthorityRequest, _facts_read, authority_target_fingerprint,
    canonical_json_bytes, canonical_timestamp,
)
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services import organism_role_definition_service as definitions
from src.intevia.services.organism_command_support import S015CommandReceiptMalformed, S015CommandReplayMismatch, _authority_request
from src.intevia.services.organism_formation_service import OrganismFormationService, FoundLivingOrganismCommand
from src.intevia.services.organism_role_service import OrganismRoleService, ProposeAssignmentCommand, TransitionAssignmentCommand, S015OperationNotYetImplemented
from src.intevia.services.organism_entitlement_service import OrganismEntitlementService
from tests.s015_0021_support import (
    database_now, found_nonroot, seed_test_gateway, register_test_learner,
    registration_terms_version_in_force, seed_fixture_substitute_authority,
)

# T06 fixture latitude only: C2's guarded writers, never its definition bypass.
from tests.test_sr_office_guards import _add_member, _propose, _consent, _lo_transition

T = datetime(2026, 10, 12, tzinfo=timezone.utc)
U = {n: UUID(int=n) for n in range(1, 10)}


def _record_words(name):
    """Name snapshot records for the person reading a test failure."""
    return {'LivingOrganism': 'the organisation', 'LivingOrganismEvent': 'organisation history', 'CircleParticipationEnd': 'Circle departure records', 'OrganismRoleDefinition': 'office definitions', 'OrganismRoleDefinitionProvision': 'constitutional citations', 'OrganismMembership': 'memberships', 'OrganismMembershipTransition': 'membership history', 'ContextualRoleAssignment': 'office appointments', 'ContextualRoleAssignmentStateEvent': 'appointment history', 'OfficeAppointmentConsent': 'nomination responses', 'CircleParticipation': 'Circle participation', 'AuthorityDecision': 'authority decisions', 'OrganismCommandReceipt': 'command receipts', 'CommandIntake': 'request records', 'OrganismEntitlement': 'organisation access grants', 'OrganismEntitlementEvent': 'access-grant history', 'OrganismEntitlementPolicyText': 'access policy text'}.get(name, "the other stored records")


def _outcome_label(result):
    """Short test diagnostic only; never dump the authority carrier or its fields."""
    if isinstance(result, S015PostFoundingAuthorityResponse):
        return "qualified"
    code = getattr(result, "refusal_code", None) or getattr(result, "code", None)
    return getattr(code, "value", code) or type(result).__name__


def _definition_action_words(command):
    """Describe existing helper callers without changing their commands or fixtures."""
    verb = {"DEFINE": "Creating", "AMEND": "Amending", "RETIRE": "Retiring"}.get(command.event_action, "Changing")
    content = getattr(command, "content", None)
    kind = getattr(content, "kind", None)
    office = {"BOARD_OFFICE": "the board office", "CUSTOM": "the custom role", "COORDINATOR": "Coordinator"}.get(kind, "the office definition")
    return f"{verb} {office} in this organisation"


def _decode(value):
    if isinstance(value, dict):
        if set(value) == {"$uuid"}:
            return UUID(value["$uuid"])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _request(spec):
    fields = {k: _decode(v) for k, v in spec.items() if k != "type"}
    for name in ("evaluated_at", "effective_at"):
        if isinstance(fields.get(name), str):
            fields[name] = datetime.fromisoformat(fields[name])
    fields["action"] = OrganismAction(fields["action"])
    for key, value in {"database_alias": "default", "actor_pk": 1, "actor_access_epoch": 0,
        "request_reference": "request:fixture", "idempotency_key": "fixture", "evaluated_at": T}.items():
        fields.setdefault(key, value)
    fields.setdefault("target_fingerprint", authority_target_fingerprint(fields.get("target") or {}))
    if spec["type"] == "pre":
        return S015PreFoundingAuthorityRequest(**fields)
    if spec["type"] == "own":
        fields.setdefault("actor_credential_pk", 1)
        fields.setdefault("living_organism_pk", 1)
        fields.setdefault("application_pk", None)
        return S015OwnApplicantAuthorityRequest(**fields)
    fields.setdefault("active_membership_head_reference", "membership-head:fixture")
    fields.setdefault("contextual_role_head_references", ())
    return S015PostFoundingAuthorityRequest(**fields)


def _post(action, target):
    return _request({"type": "post", "action": action, "command_subtype": action,
        "actor_identity_id": U[1], "living_organism_id": U[2], "effective_at": T, "target": target})


def _definition_target(organism_uuid, content, action="DEFINE", row=None):
    """Independent target fixture: validate it before calling the provider directly."""
    return {**asdict(content), "action":"RECORD_ROLE_DEFINITION",
        "schema":"intevia.s015.role-definition-target.v1", "event_action":action,
        "living_organism_id":str(organism_uuid), "role_uuid":None if row is None else str(row.role_uuid),
        "definition_version":1 if row is None else row.definition_version + (action=="AMEND"),
        "supersedes_role_uuid":str(row.role_uuid) if action=="AMEND" else None,
        "retirement_reason":"Test retirement" if action=="RETIRE" else None}


class SRDefinitionVocabularyTests(SimpleTestCase):
    def test_01_v1_corpus_is_read_only_and_reproduces_installed_facts(self):
        """O-07 compatibility: replay all archived corpus cases, without running its suite."""
        path = Path(__file__).resolve().parents[1] / "archive/tests_2026_10/fixtures/s015_authority_facts_corpus_v1.json"
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "6ca09d2a1d642e70ab3a4892cb88d0aa18eb9dab8aed014f06959b27fc6a4e38")
        corpus = json.loads(raw)
        self.assertEqual(corpus["schema"], "S015_AUTHORITY_FACTS_V1")
        for case in corpus["cases"]:
            with self.subTest(case=case["id"]):
                request = _request(case["request"])
                principal = SimpleNamespace(principal_uuid=UUID(case["principal"])) if case.get("principal") else None
                if case["group"] == "NEG":
                    with self.assertRaises(S015AuthorityMalformed):
                        _facts_read(request, case["reference"], principal)
                    continue
                self.assertEqual(canonical_json_bytes(_facts_read(request, case["reference"], principal)).decode(), case["expected_after"])
                if case["reference"] and case["reference"].startswith("s015a1:"):
                    if case["group"] == "PP":
                        content = json.loads(case["reference"][7:])
                        self.assertEqual(ProductionApplicationAuthorityProvider._encode_facts(request, content, refusal_code=content.get("x")), case["reference"])

    def test_02_v2_forms_round_trip_exact_facts_and_reject_cross_form_keys(self):
        """O-07: named new forms, AR derivation and isolated longest-form boundaries."""
        self.assertEqual(S015_AUTHORITY_FACTS_SCHEMA, "S015_AUTHORITY_FACTS_V2", msg='Encoding the new office decisions should retain the agreed version-two facts format; a different schema was selected.')
        # Exact new contract row, not a frozen whole-suite or system count.
        self.assertEqual({plane.value:(binding.state.value,binding.reason) for plane,binding in qualification_planes("RECORD_ROLE_DEFINITION").items()}, {
            "L":("NOT_APPLICABLE_WITH_REASON","NO_LEGAL_CONCLUSION"), "C":("PREREQUISITE_REQUIRED",None),
            "E":("PREREQUISITE_REQUIRED",None), "P":("FORBIDDEN",None),
        }, msg='Recording an office definition should require constitutional and execution authority, forbid propagation, and make no legal conclusion; the authority requirements differ.')
        transition = {"a": str(U[3]), "g": str(U[4]), "k": "ASSIGN", "o": str(U[5]), "p": str(U[6]), "u": "c:" + str(U[7]), "y": "AR"}
        sr = {"m": str(U[3]), "n": str(U[4]), "t": "EFFECTIVE_COORDINATOR", "k": "C", "i": None, "o": str(U[5]), "z": "CUSTOM_ROLES", "e": str(U[6])}
        co = {"m": str(U[3]), "n": str(U[4]), "t": "EFFECTIVE_COORDINATOR", "k": "C", "i": None, "o": str(U[5]), "c": str(U[6])}
        definition = {"g": str(U[3]), "c": str(U[4]), "o": str(U[5]), "z": "CUSTOM_ROLES", "i": None}
        cases = [("TRANSITION_CONTEXTUAL_ROLE", {"event_action": "ASSIGN"}, transition, 247),
            ("TRANSITION_CONTEXTUAL_ROLE", {"event_action": "PROPOSE", "role_definition_uuid": str(U[7]), "nomination_rule_source": "DEFINITION", "kind": "CUSTOM", "participation_required": True}, sr, 244),
            ("TRANSITION_CONTEXTUAL_ROLE", {"event_action": "PROPOSE", "role_definition_uuid": str(U[7])}, co, 225)]
        cases += [("TRANSITION_CONTEXTUAL_ROLE", {"event_action": act},
            {"a":str(U[3]),"g":str(U[4]),"k":act,"o":str(U[5])},152) for act in ("END","REASSIGN")]
        cases += [("RECORD_OFFICE_CONSENT", {"event_action":"CONSENT"},
            {"a":str(U[3]),"p":str(U[4]),"g":str(U[5]),"k":"CONSENT","u":str(U[6]),"w":str(U[7])},238)]
        cases += [("RECORD_ROLE_DEFINITION", {"event_action": act, "kind":"CUSTOM", "nomination_basis":"CUSTOM"}, {k:v for k,v in definition.items() if k != "i"} if act != "RETIRE" else {k: definition[k] for k in ("g", "c", "o")}, 165 if act != "RETIRE" else 137) for act in ("DEFINE", "AMEND", "RETIRE")]
        for action, target, facts, declared_max in cases:
            with self.subTest(action=action, form=target["event_action"]):
                request = _post(action, target)
                reference = ProductionApplicationAuthorityProvider._encode_facts(request, facts, refusal_code=None)
                self.assertLessEqual(len(reference), 255, msg="Encoding this office action's decision should fit the 255-character record limit; the reference is too long.")
                self.assertLessEqual(len(reference), declared_max, msg="Encoding this office action's decision should fit its declared maximum length; the reference exceeds that action's limit.")
                parsed = _facts_read(request, reference)
                observed = [(f["relation"], f["reference"]) for f in parsed[2:]]
                expected = []
                if target["event_action"] == "PROPOSE":
                    expected.append(("core_organismroledefinition", str(U[7])))
                relations = {"a": "core_contextualroleassignment", "g": "core_organismroledefinition",
                    "c": "core_logovernanceconfigurationversion", "o": "core_contextualroleassignmentstateevent",
                    "p": "core_contextualroleassignmentstateevent", "m": "core_organismmembershiptransition",
                    "n": "core_organismmembershiptransition", "i": "s2b_coordinator_nomination_entitlement",
                    "z": "s015_entitlement_effective", "e": "core_circleparticipation"}
                for key, value in sorted(facts.items()):
                    if key == "k" and target["event_action"] == "PROPOSE":
                        expected.append(("sr_nomination_policy" if "nomination_rule_source" in target else "s2b_coordinator_nomination_policy", value))
                    elif key == "u": expected.append(("core_organismmembership",str(U[6])) if action == "RECORD_OFFICE_CONSENT" else ("core_officeappointmentconsent",str(U[7])))
                    elif key == "w": expected.append(("core_officeappointmentconsent",str(U[7])))
                    elif key == "y": expected.append(("core_organismroledefinitionprovision", str(U[4]) + ":ALTERNATIVE_ROUTE"))
                    elif key in relations: expected.append((relations[key], "governance-configuration-version:" + value if key == "c" else value))
                self.assertEqual(observed, expected, msg="Reading this office action's decision back should preserve every declared evidence link and its source; the recovered links differ.")
                refusal = {"x": "C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED"}
                ref = ProductionApplicationAuthorityProvider._encode_facts(request, refusal, refusal_code=refusal["x"])
                self.assertLessEqual(len(ref), 255, msg='Encoding the Coordinator-recovery refusal should fit the 255-character record limit; the refusal reference is too long.')
                self.assertEqual(_facts_read(request, ref)[2:], [], msg='Reading the bare Coordinator-recovery refusal should not invent supporting evidence; extra evidence links appeared.')
        # Long refusal carries no g/y; its remaining provenance must round-trip exactly.
        request = _post("TRANSITION_CONTEXTUAL_ROLE", {"event_action": "ASSIGN"})
        long_refusal = {"x":"C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED",**{k:v for k,v in transition.items() if k not in {"g","y"}}}
        encoded = ProductionApplicationAuthorityProvider._encode_facts(request,long_refusal,refusal_code=long_refusal["x"])
        self.assertLessEqual(len(encoded),240, msg='Encoding the appointment refusal with its permitted evidence should fit the 240-character boundary example; the reference is too long.')
        self.assertEqual([(f["relation"],f["reference"]) for f in _facts_read(request,encoded)[2:]],[
            ("core_contextualroleassignment",str(U[3])),("core_contextualroleassignmentstateevent",str(U[5])),
            ("core_contextualroleassignmentstateevent",str(U[6])),("core_officeappointmentconsent",str(U[7])),
        ], msg='Reading the appointment refusal should preserve its appointment, proposal and consent evidence without adding a definition or alternative route; the links differ.')
        for invalid in ({k:v for k,v in transition.items() if k != "g"}, {**transition,"y":"WRONG"}):
            with self.assertRaises(S015AuthorityFactsUndeclared, msg='Validating an appointment decision with a missing definition or invalid alternative route should reject the malformed facts; those facts were accepted.'): validate_authority_facts(request, invalid, refusal_code=None)
            with self.assertRaises(S015AuthorityMalformed, msg='Reading an appointment decision with a missing definition or invalid alternative route should reject the malformed record; that record was accepted.'): _facts_read(request, "s015a1:" + canonical_json_bytes(invalid).decode())
        sr_request = _post("TRANSITION_CONTEXTUAL_ROLE", cases[1][1])
        co_request = _post("TRANSITION_CONTEXTUAL_ROLE", cases[2][1])
        for request, facts in ((sr_request, {**sr,"c":str(U[8])}), (sr_request, {**sr,"d":"C"}), (co_request,sr), (sr_request,{k:v for k,v in sr.items() if k != "e"})):
            with self.assertRaises(S015AuthorityFactsUndeclared, msg='Validating a nomination decision should reject facts borrowed from another nomination form or missing required participation; those facts were accepted.'): validate_authority_facts(request, facts, refusal_code=None)
        bad_source = _post("TRANSITION_CONTEXTUAL_ROLE", {**cases[1][1],"nomination_rule_source":"CALLER"})
        self.assertIsNone(role_event_form(bad_source), msg='Selecting a nomination form should reject a caller-invented rule source; a form was selected for that source.')
        self.assertIsNone(role_event_form(_post("TRANSITION_CONTEXTUAL_ROLE", {"event_action":"PROPOSE","nomination_rule_source":[]})), msg='Selecting a nomination form should reject a list where the rule source must be a name; a form was selected for the invalid value.')
        validate_authority_facts(bad_source, {"x":"UNRECOGNISED_ACTION"}, refusal_code="UNRECOGNISED_ACTION")

        valid_retire = _definition_target(U[2],definitions.DefinitionContent(code="TEST_BOARD"),"RETIRE",
            SimpleNamespace(role_uuid=U[3],definition_version=1))
        self.assertTrue(definitions.definition_target_valid(valid_retire,U[2]), msg='Validating retirement of the named board-office definition should accept its complete target; the valid target was rejected.')
        for key in ("code","kind"):
            for bad in (None, ""):
                self.assertFalse(definitions.definition_target_valid({**valid_retire,key:bad},U[2]), msg='Validating retirement should refuse a missing office identity or kind; the incomplete target was accepted.')
        for action in ("DEFINE","AMEND"):
            request = _post("RECORD_ROLE_DEFINITION",{"event_action":action,"kind":"CUSTOM"})
            validate_authority_facts(request,{k:v for k,v in definition.items() if k != "i"},refusal_code=None)
            with self.assertRaises(S015AuthorityFactsUndeclared, msg='Creating or amending a custom-role definition should reject facts without custom-role access; the incomplete decision facts were accepted.'):
                validate_authority_facts(request,{k:v for k,v in definition.items() if k != "z"},refusal_code=None)
        source = Path(definitions.__file__).read_text()
        codes = {n.args[0].value for n in ast.walk(ast.parse(source))
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
            and n.func.id=="S015RoleDefinitionRefused" and n.args and isinstance(n.args[0],ast.Constant)}
        self.assertEqual(codes,set(definitions.DEFINITION_REFUSAL_MESSAGES), msg='Checking office-definition refusals against their explanations should find an explanation for every refusal and no unused entry; the two sets differ.')
        for code,(message,category) in definitions.DEFINITION_REFUSAL_MESSAGES.items():
            self.assertIn(category,("A","B","C"), msg='Classifying an office-definition refusal should use one of the three agreed support classes; an unsupported class was found.')
            self.assertEqual(definitions.S015RoleDefinitionRefused(code,message).support_class,category, msg='Constructing an office-definition refusal should preserve the support class assigned to its explanation; the exception reports another class.')
        for code,(message,category) in definitions.DEFINITION_AUTHORITY_MESSAGES.items():
            self.assertIn(category,("A","B","C"), msg='Classifying an authority refusal for an office definition should use one of the three agreed support classes; an unsupported class was found.')
            self.assertTrue(message, msg='Explaining an authority refusal for an office definition should provide a nonempty message; the explanation is blank.')
        provider_source = Path(__import__(ProductionApplicationAuthorityProvider.__module__,fromlist=["_"]).__file__).read_text()
        provider_tree = ast.parse(provider_source)
        providers = [n for n in ast.walk(provider_tree) if isinstance(n,ast.FunctionDef)
            and n.name in {"_coordinator_act","_qualify_role_definition"}]
        provider_codes = {n.attr for f in providers for n in ast.walk(f)
            if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name)
            and n.value.id=="S015AuthorityRefusalCode"}
        self.assertEqual(provider_codes,set(definitions.DEFINITION_AUTHORITY_MESSAGES)
            # The identity gate is in the common dispatcher, outside these two methods.
            | (set(definitions.DEFINITION_STATE_REFUSALS) - {"E_IDENTITY_INACTIVE"}) | {"UNRECOGNISED_ACTION"}, msg='Checking the definition provider against its explanation maps should account for every provider refusal and the separately handled identity gate; the listed rules differ.')
        self.assertEqual(definitions.S015RoleDefinitionMalformed("test only").support_class,"C", msg='Classifying malformed office-definition input should retain technical support class C; another class was returned.')
        self.assertEqual(set(definitions.DEFINITION_STATE_MESSAGES),definitions.DEFINITION_STATE_REFUSALS, msg='Checking organisation-state refusals should find exactly the seven mapped explanations; the message and refusal lists differ.')
        self.assertEqual(definitions.DEFINITION_STATE_MESSAGES["C_CONFIGURATION_NOT_ESTABLISHED"][1],"A", msg='Explaining missing governance settings should remain actionable support class A because an effective Coordinator can establish them; another class was recorded.')
        for text,category in definitions.DEFINITION_STATE_MESSAGES.values():
            self.assertIn(category,("A","B","C"), msg='Classifying an organisation-state explanation should use an agreed support class; an unsupported class was found.')
            self.assertNotEqual(text,"organism authority denied", msg='Explaining an organisation-state refusal should give specific guidance instead of the generic authority-denied text; only the generic text was provided.')
        from core.organism_authority_facts import validate_definition_nomination_facts
        # i is required as evidence for SILENT A/B, not for CUSTOM policy C.
        silent_request = _post("TRANSITION_CONTEXTUAL_ROLE", {**cases[1][1], "kind":"BOARD_OFFICE", "participation_required":False, "nomination_basis":"SILENT"})
        silent_facts = {"m":str(U[3]), "n":str(U[4]), "t":"MEMBER_NOMINATION", "k":"A", "i":str(U[8])}
        validate_authority_facts(silent_request, silent_facts, refusal_code=None)
        for key in ("m", "n", "o", "e", "i"):
            with self.subTest(required_reference=key):
                request, facts = (silent_request, silent_facts) if key == "i" else (sr_request, sr)
                with self.assertRaises(S015AuthorityFactsUndeclared, msg='Validating nomination evidence should reject a placeholder where the selected policy requires a membership, office, participation or entitlement reference; the placeholder was accepted.'):
                    validate_authority_facts(request, {**facts, key:"-"}, refusal_code=None)
        for target, facts in (
            ({"kind":"BOARD_OFFICE", "participation_required":False, "nomination_basis":"CONSTITUTION"}, sr),
            ({"kind":"CUSTOM", "participation_required":True, "nomination_basis":"CUSTOM"}, {**sr,"i":"invented"}),
        ):
            with self.assertRaises(S015AuthorityFactsUndeclared, msg='Validating nomination facts against the recorded role should reject a custom-access claim for a board office or invented nomination entitlement; the inconsistent facts were accepted.'):
                validate_definition_nomination_facts(facts,target)
        for action in ("DEFINE", "AMEND", "RETIRE"):
            request = _post("RECORD_ROLE_DEFINITION",{"event_action":action,"kind":"BOARD_OFFICE","nomination_basis":"SILENT","nomination_policy":"C"})
            baseline = {"g":"-" if action == "DEFINE" else str(U[3]), "c":str(U[4]), "o":str(U[5])}
            validate_authority_facts(request,baseline,refusal_code=None)
            for key in ("c","o") + (() if action == "DEFINE" else ("g",)):
                with self.assertRaises(S015AuthorityFactsUndeclared, msg='Validating an office-definition decision should reject a placeholder in a required definition, governance-settings or Coordinator reference; the placeholder was accepted.'):
                    validate_authority_facts(request,{**baseline,key:"-"},refusal_code=None)
            if action != "RETIRE":
                for extra in ({"z":"CUSTOM_ROLES"},{"i":str(U[6])}):
                    with self.assertRaises(S015AuthorityFactsUndeclared, msg='Creating or amending a board office under silent policy C should reject custom-role or nomination-entitlement facts; the extra entitlement was accepted.'):
                        validate_authority_facts(request,{**baseline,**extra},refusal_code=None)



class SRDefinitionCommandTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.assertEqual(connection.vendor, "postgresql", "C3 requires the PostgreSQL route")
        self.tag = uuid4().hex
        with transaction.atomic(), connection.cursor() as cursor:
            self.ids = found_nonroot(cursor, tag="c3-" + self.tag)
            seed_test_gateway(cursor, self.ids, include_governance_configuration=True)
        self.organism = models.LivingOrganism.objects.get(pk=self.ids["lo"])
        self.actor = models.Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self.provider = ProductionApplicationAuthorityProvider()
        self.authority = S015Authority(provider=self.provider)
        self.service = definitions.OrganismRoleDefinitionService(authority=self.authority, clock=database_now)

    def _command(self, action="DEFINE", *, row=None, content=None):
        at = database_now()
        key = uuid4().hex
        if content is None and action != "RETIRE":
            content = definitions.DefinitionContent(code="TEST_C3_" + key[:16], provisions=(
                definitions.DefinitionProvision("OFFICE_BASIS", "library:test-c3:v1#office", "v1"),))
        return definitions.RecordRoleDefinitionCommand(self.actor.credential, self.organism.organism_id,
            action, "c3:" + key, key, at, at, content=content,
            role_definition_uuid=None if row is None else row.role_uuid,
            retirement_reason="Test retirement" if action == "RETIRE" else None)

    def _snapshot(self):
        # All concrete fields, not just counts; table scope is disclosed in the ledger.
        names = ("LivingOrganism", "LivingOrganismEvent", "CircleParticipationEnd", "OrganismRoleDefinition", "OrganismRoleDefinitionProvision", "OrganismMembership",
            "OrganismMembershipTransition", "ContextualRoleAssignment", "ContextualRoleAssignmentStateEvent",
            "OfficeAppointmentConsent", "CircleParticipation", "AuthorityDecision", "OrganismCommandReceipt", "CommandIntake",
            "OrganismEntitlement", "OrganismEntitlementEvent", "OrganismEntitlementPolicyText")
        return {name: list(getattr(models, name).objects.order_by("pk").values_list()) for name in names}

    def _success(self, command, *, action_description=None):
        action_description = action_description or _definition_action_words(command)
        try:
            return self.service.record_role_definition(command)
        except (DatabaseError, S015AuthorityDenied, definitions.S015RoleDefinitionError) as exc:
            self.fail(f"{action_description}: expected qualified and committed; actual {_outcome_label(exc)}.")

    def _failure(self, command, expected, *, action_description=None, expected_code=None):
        action_description = action_description or _definition_action_words(command)
        expected_outcome = expected_code or expected.__name__
        try:
            self.service.record_role_definition(command)
        except (DatabaseError, S015AuthorityDenied, definitions.S015RoleDefinitionError,
                S015CommandReplayMismatch, S015CommandReceiptMalformed) as exc:
            if not isinstance(exc, expected):
                self.fail(f"{action_description}: expected refusal {expected_outcome}; actual {_outcome_label(exc)}.")
            return exc
        self.fail(f"{action_description}: expected refusal {expected_outcome}; actual qualified and committed.")

    def _unchanged_except(self, before, *allowed, action_description="Completing the office-definition action in this organisation"):
        after = self._snapshot()
        for name in before:
            if name not in allowed and after[name] != before[name]:
                self.fail(f"{action_description} should leave {_record_words(name)} unchanged; those records changed.")
        return after

    def _define(self):
        command = self._command()
        result = self._success(command)
        return command, models.OrganismRoleDefinition.objects.get(role_uuid=result.role_definition_uuid)


    def _custom_entitlement(self, *, action_description="Preparing complimentary custom-role access for this organisation"):
        """Fixture latitude: installed beta composer, not an opt-in/paid-lineage test.

        All guards active, including deferred first-GRANTED-event requirement.
        The existing FOUND event identifies the fixture's originating record;
        policy text is explicitly test-only. No archive module is imported.
        """
        if models.OrganismEntitlement.objects.filter(living_organism=self.organism).exists():
            self.assertTrue(definitions.custom_role_entitled(self.organism.organism_id,database_now(),"default"), msg=f"{action_description} should confirm the existing access grant; the organisation was not entitled.")
            return
        text = "TEST ONLY C3 entitlement fixture; not a user acceptance."
        with transaction.atomic():
            policy = models.OrganismEntitlementPolicyText.objects.create(policy_version=1000003,
                accepted_text=text,text_sha256=hashlib.sha256(text.encode()).hexdigest())
            at = database_now()
            source = models.LivingOrganismEvent.objects.get(pk=self.ids["events"]["core_livingorganismevent"])
            OrganismEntitlementService(authority=self.authority,clock=database_now)._compose_beta_grant(
                actor=self.actor,organism=self.organism,policy=policy,effective_at=at,
                received_at=at,composed_from_event_uuid=source.event_uuid)
        self.assertTrue(definitions.custom_role_entitled(self.organism.organism_id,database_now(),"default"), msg=f"{action_description} should make the new access grant effective; the organisation was not entitled.")

    def _define_custom(self):
        self._custom_entitlement()
        command = self._command()
        command = replace(command,content=replace(command.content,kind="CUSTOM",nomination_basis="CUSTOM",provisions=()))
        result = self._success(command)
        return command, models.OrganismRoleDefinition.objects.get(role_uuid=result.role_definition_uuid)

    def _fixture_holder(self, row, *, action_description="Appointing a consenting nominee to the fixture office"):
        """C2 guarded appointment fixture; production non-Coordinator route stays closed."""
        tag = "c3-holder:"+uuid4().hex
        with transaction.atomic(), connection.cursor() as cursor:
            member_id, nominee_id, member_uuid = _add_member(cursor,self.ids,tag,database_now())
        with transaction.atomic(), connection.cursor() as cursor:
            assignment_id, assignment_uuid, proposal_id = _propose(cursor,self.ids,
                proposer_id=self.actor.pk,proposer_membership_id=self.ids["membership"],
                nominee_membership_id=member_id,nominee_membership_uuid=member_uuid,
                definition_id=row.pk,definition_uuid=row.role_uuid,at=database_now(),tag=tag)
        with transaction.atomic(), connection.cursor() as cursor:
            _consent(cursor,self.ids,nominee_identity_id=nominee_id,nominee_membership_id=member_id,
                nominee_membership_uuid=member_uuid,assignment_id=assignment_id,assignment_uuid=assignment_uuid,
                proposal_id=proposal_id,at=database_now(),tag=tag)
        with transaction.atomic(), connection.cursor() as cursor:
            _lo_transition(cursor,self.ids,enterer_id=self.actor.pk,enterer_membership_id=self.ids["membership"],
                assignment_id=assignment_id,assignment_uuid=assignment_uuid,predecessor_id=proposal_id,
                sequence=2,action="ASSIGN",prior_state="PROPOSED",resulting_state="ACTIVE",at=database_now(),tag=tag)
        self.assertEqual(models.ContextualRoleAssignment.objects.get(pk=assignment_id).state, "ACTIVE", msg=f"{action_description} should leave that appointment active; its state differs.")
        return assignment_id

    def _provider_refusal(self, content, code, *, action="DEFINE", row=None, action_description=None):
        """Call the real definition qualifier directly; no service guard masks it.

        Direct call is needed because the service refuses Coordinator, INTEVIA_,
        BOARD RETIRE and kind changes before it asks for a decision.
        This is not an end-to-end test of the provider's earlier identity/LO gates
        or S015Authority decision persistence. F0's identity history is incomplete.
        """
        action_description = action_description or _definition_action_words(SimpleNamespace(event_action=action, content=content))
        target = _definition_target(self.organism.organism_id,content,action,row)
        self.assertTrue(definitions.definition_target_valid(target,self.organism.organism_id), msg=f"{action_description} should present a valid office-definition request to the provider; the test request is malformed.")
        member = models.OrganismMembership.objects.select_related("head_transition").get(pk=self.ids["membership"])
        office = models.ContextualRoleAssignment.objects.select_related("head_state_event").get(pk=self.ids["assignments"]["COORDINATOR"])
        at = database_now()
        request = _authority_request(database_alias="default",actor=self.actor,actor_membership=member,
            actor_role_head_refs=(office.head_state_event.lineage_reference,),action=OrganismAction.RECORD_ROLE_DEFINITION,
            request_reference="c3-provider:"+uuid4().hex,idempotency_key=uuid4().hex,received_at=at,effective_at=at,
            target=target,target_fingerprint=authority_target_fingerprint(target),living_organism_id=self.organism.organism_id)
        before = self._snapshot()
        result = self.provider._qualify_role_definition(request,self.organism,member,office)
        if not isinstance(result, S015PostFoundingAuthorityRefusal) or result.refusal_code.value != code:
            self.fail(f"{action_description}: expected refusal {code}; actual {_outcome_label(result)}.")
        if self._snapshot() != before:
            self.fail(f"{action_description} should only read records; stored records changed while returning {_outcome_label(result)}.")

    def _class_c_refusal(self, command, code, text, *, action_description=None):
        action_description = action_description or _definition_action_words(command)
        before = self._snapshot()
        error = self._failure(command, definitions.S015RoleDefinitionRefused,
                              action_description=action_description, expected_code=code)
        if error.code != code:
            self.fail(f"{action_description}: expected refusal {code}; actual {_outcome_label(error)}.")
        self.assertEqual(str(error), text, msg=f"{action_description} should explain why the office cannot be changed and give the approved next step; the explanation differs.")
        self._unchanged_except(before, "CommandIntake", action_description=action_description)
        return error

    def test_03_definition_lifecycle_keeps_history_and_creates_no_office(self):
        """O-22/O-18: definition lifecycle preserves history; explicit office commands appoint."""
        self._custom_entitlement()
        before = self._snapshot()
        command, first = self._define_custom()
        self._unchanged_except(before,"OrganismRoleDefinition","OrganismRoleDefinitionProvision","AuthorityDecision","OrganismCommandReceipt","CommandIntake",
            action_description="Defining the custom role without appointing anyone")
        self._class_c_refusal(self._command("AMEND",row=first,content=replace(command.content,
            kind="BOARD_OFFICE",nomination_basis="SILENT",provisions=(
                definitions.DefinitionProvision("OFFICE_BASIS","library:test-c3:v1#office","v1"),))),
            "SR_DEFINITION_KIND_FIXED",'You cannot change this office from a board office to a custom role, or from a custom role to a board office. An office keeps the kind it was recorded with, and its history is kept. No changes have been made to the office.', action_description='Trying to change the custom role into a board office')
        self._class_c_refusal(self._command(content=command.content),"SR_DEFINITION_EXISTS",'This office could not be created: this organisation already has an office with this identity. No office has been created or changed. To change how the existing office is defined, amend it.', action_description='Trying to create a second definition for the same custom role')
        first_content = {key: getattr(first,key) for key in ("code","kind","nomination_policy","authority_decision_reference","recorded_at")}
        amend = self._command("AMEND", row=first, content=replace(command.content, participation_required=True,
            provisions=command.content.provisions + (definitions.DefinitionProvision("PARTICIPATION_REQUIRED","library:test-c3:v1#participation","v1"),)))
        # C5 replaces the old IL-8 branch with real nomination, appointment and
        # ending, so no unfinished nomination blocks this lifecycle's RETIRE.
        role_service = OrganismRoleService(authority=self.authority, clock=database_now)
        def office_success(call, action):
            try: return call()
            except Exception as exc:
                self.fail(f"{action} should complete through the real office route; it instead raised {type(exc).__name__}.")
        at = database_now()
        nominee = models.OrganismMembership.objects.get(pk=self.ids["membership"])
        proposed = office_success(lambda:role_service.propose_assignment(ProposeAssignmentCommand(
            credential=self.actor.credential, request_reference="c5-d03-propose:"+self.tag,
            idempotency_key="c5-d03-propose:"+self.tag, occurred_at=at,effective_at=at,
            membership_uuid=nominee.membership_uuid,role_definition_uuid=first.role_uuid,
            effective_until_state="NOT_ESTABLISHED",effective_until=None)),"Nominating the member")
        self.assertTrue(models.OrganismCommandReceipt.objects.filter(lineage_reference=proposed.lineage_reference).exists(),
                        "Nominating for the custom role should commit its receipt; the receipt is missing.")
        for action,state in (("ASSIGN","ACTIVE"),("END","ENDED")):
            at=database_now();key=uuid4().hex
            outcome=office_success(lambda:role_service.transition_assignment(TransitionAssignmentCommand(
                credential=self.actor.credential,request_reference="c5-d03-"+action+":"+key,idempotency_key=key,
                occurred_at=at,effective_at=at,assignment_uuid=proposed.assignment_uuid,action=action,resulting_state=state,
                effective_until_state="NONE" if action=="ASSIGN" else None,effective_until=None)),"Appointing or ending the member")
            self.assertTrue(models.OrganismCommandReceipt.objects.filter(lineage_reference=outcome.lineage_reference).exists(),
                            "Appointing and then ending the custom-role self-nomination should commit each receipt; a receipt is missing.")
        self.assertEqual(models.ContextualRoleAssignment.objects.get(assignment_uuid=proposed.assignment_uuid).state,"ENDED",
                         "Finishing the custom-role appointment should leave it ended before retirement; it remains open.")
        after_office_acts = self._snapshot()
        second_result = self._success(amend, action_description='Amending the custom role to require GOVERNANCE participation')
        second = models.OrganismRoleDefinition.objects.get(role_uuid=second_result.role_definition_uuid)
        self.assertEqual((second.definition_version,second.supersedes_id), (first.definition_version+1,first.pk), msg='Amending the custom role should create the next version linked to the original definition; its version or predecessor differs.')
        first.refresh_from_db()
        self.assertFalse(first.active, msg='Amending the custom role should mark the original definition as no longer current; the old version remained active.')
        self.assertEqual({k:getattr(first,k) for k in first_content},first_content, msg="Amending the custom role should preserve the original definition's content and attribution; historical fields changed.")
        self._class_c_refusal(self._command("AMEND",row=first,content=command.content),"SR_SUPERSEDED_DEFINITION","This office's definition has been updated since the version you were working from. No changes have been made. Open the current definition and make the change there.", action_description='Trying to amend the superseded custom-role version')
        retired = self._success(self._command("RETIRE",row=second), action_description='Retiring the current custom-role definition')
        second.refresh_from_db()
        self.assertFalse(second.active, msg='Retiring the current custom-role definition should mark it inactive; the retired version remained active.')
        self.assertIsNotNone(second.retired_recorded_at, msg='Retiring the custom-role definition should record when retirement was stored; that time is missing.')
        self.assertEqual(len(retired.lineage_reference), 71, msg='Recording the custom-role retirement should return the agreed 71-character history reference; its length differs.')
        self.assertEqual(second.retired_by_decision_reference, models.OrganismCommandReceipt.objects.get(lineage_reference=retired.lineage_reference).authority_decision_reference, msg='Retiring the custom role should attribute the retired version to the same decision as its receipt; the decision references differ.')
        self._unchanged_except(before,"OrganismRoleDefinition","OrganismRoleDefinitionProvision","AuthorityDecision","OrganismCommandReceipt","CommandIntake","ContextualRoleAssignment","ContextualRoleAssignmentStateEvent", action_description='Creating and retiring a custom role after explicitly appointing and ending its holder')
        self._unchanged_except(after_office_acts,"OrganismRoleDefinition","OrganismRoleDefinitionProvision","AuthorityDecision","OrganismCommandReceipt","CommandIntake",
            action_description="Amending and retiring the definition without changing the ended appointment")
        for action in ("AMEND","RETIRE"):
            self._class_c_refusal(self._command(action,row=second,content=command.content if action=="AMEND" else None),
                "SR_DEFINITION_RETIRED",'This office has been retired, so it cannot be changed, retired again, or re-established here. No changes have been made. Its definition and history remain on record.', action_description='Trying to amend or retire the already retired custom-role version')
        again = self._success(command, action_description='Replaying the original custom-role creation request')
        self.assertEqual(again.role_definition_uuid,first.role_uuid, msg='Repeating the original custom-role creation request should return its original definition; the replay returned another version.')
        self._class_c_refusal(self._command(content=command.content),"SR_DEFINITION_RETIRED",'This office has been retired, so it cannot be changed, retired again, or re-established here. No changes have been made. Its definition and history remain on record.', action_description='Trying to recreate the retired custom role')
        self.assertEqual(models.OrganismRoleDefinition.objects.filter(code=first.code,living_organism=self.organism).count(),2, msg='Repeating creation and refusing re-creation of the retired custom role should leave exactly its two historical versions; the version count differs.')

    def test_04_cq_refusal_on_all_three_acts_retains_only_attempt_evidence(self):
        """O-07: class A matrix for A1/A2/A3; fixture CQ read is injected.

        The patch sees every effective-version read during the command. Only a
        governance-configuration object is changed; the exact C_ROUTE_IS_CQ
        assertion exposes a different earlier refusal rather than accepting it.
        """
        command, row = self._define_custom()
        original = __import__("src.intevia.services.organism_application_authority",fromlist=["_effective_version"])._effective_version
        def cq(*args,**kwargs):
            config = original(*args,**kwargs)
            if config is not None and isinstance(config,models.LOGovernanceConfigurationVersion):
                config.cq_active = True
            return config
        for action in ("DEFINE","AMEND","RETIRE"):
            candidate = self._command(action,row=row if action != "DEFINE" else None,
                content=command.content if action != "RETIRE" else None)
            before = self._snapshot()
            with patch("src.intevia.services.organism_application_authority._effective_version",side_effect=cq):
                error = self._failure(candidate,S015AuthorityDenied)
            self.assertEqual(error.refusal_code.value,"C_ROUTE_IS_CQ")
            self.assertEqual(str(error),'This change to an office definition could not be made by the Coordinators alone: this organisation decides such matters through its Collective Questioning process. No office has been created or changed; the request and this refusal are on record. Raise it through that process.')
            after = self._unchanged_except(before,"AuthorityDecision","CommandIntake")
            self.assertEqual(len(after["AuthorityDecision"]),len(before["AuthorityDecision"])+1)
            self.assertEqual(len(after["CommandIntake"]),len(before["CommandIntake"])+1)
            decision = models.AuthorityDecision.objects.get(decision_reference=error.authority_decision_reference)
            self.assertEqual((decision.outcome,decision.target["event_action"]),("REFUSED",action))
            intake = models.CommandIntake.objects.get(idempotency_key=candidate.idempotency_key)
            self.assertEqual(decision.evaluated_at,intake.received_at)
            coordinates = {"effective_at":canonical_timestamp(candidate.effective_at),"evaluated_at":canonical_timestamp(intake.received_at)}
            self.assertEqual(decision.facts_read,[
                {"relation":"core_identity","reference":str(self.actor.identity_id),"eligible_at":coordinates},
                {"relation":"core_livingorganism","reference":str(self.organism.organism_id),"eligible_at":coordinates},
            ])

    def test_05_reserved_and_entitlement_refusals_precede_writes(self):
        """Reserved service/provider gates separately; absent entitlement at provider."""
        base = self._command().content
        reserved = replace(base,code="INTEVIA_TEST_RESERVED")
        before = self._snapshot()
        with patch.object(self.service,"_evaluate_authority",wraps=self.service._evaluate_authority) as evaluate:
            error = self._failure(self._command(content=reserved),definitions.S015RoleDefinitionRefused)
        evaluate.assert_not_called()
        self.assertEqual(error.code,"SR_ROOT_OFFICE_RESERVED")
        self.assertEqual(str(error),"This is a reserved INTEVIA root office. It is not defined, changed, retired or filled through this route; its route is the platform's root founding. Nothing has changed.")
        self._unchanged_except(before,"CommandIntake")
        self._provider_refusal(reserved,"C_RESERVED_ROOT_OFFICE")
        self._provider_refusal(definitions.DefinitionContent(code="COORDINATOR",kind="COORDINATOR"),"C_COORDINATOR_DEFINITION_RESERVED")
        cases = [(replace(base,nomination_policy="A"),"E_NOMINATION_POLICY_NOT_ENTITLED"),
            (replace(base,nomination_policy="B"),"E_NOMINATION_POLICY_NOT_ENTITLED"),
            (replace(base,kind="CUSTOM",nomination_basis="CUSTOM",provisions=()),"E_CUSTOM_ROLE_NOT_ENTITLED")]
        for content, code in cases:
            before = self._snapshot()
            error = self._failure(self._command(content=content),S015AuthorityDenied)
            self.assertEqual(error.refusal_code.value,code)
            if code == "E_CUSTOM_ROLE_NOT_ENTITLED":
                self.assertEqual(str(error),"This custom role could not be recorded. Custom roles are a subscription capability, and this organisation does not have an active subscription or complimentary access at this time. No custom role has been created or changed; the request and this refusal are on record. Your constitution's board offices can be recorded free. Existing custom-role appointments continue; new ones cannot start.")
            self._unchanged_except(before,"AuthorityDecision","CommandIntake")

    def test_06_class_c_missing_citation_and_retirement_holder_rollback(self):
        """O-22 / F-20 A: exact class-C refusals and preserved founding Coordinator.

        Holder fixture latitude: define entitled CUSTOM through the real C3 service;
        use C2's test provider and direct PROPOSE/CONSENT/ASSIGN writers because
        the production non-Coordinator appointment route stays closed until C5.
        All installed guards stay active; each fixture stage commits before the
        next database clock reading. A distinct nominee owes explicit consent.
        """
        candidate = self._command()
        before = self._snapshot()
        error = self._failure(replace(candidate,content=replace(candidate.content,provisions=())),definitions.S015RoleDefinitionRefused, action_description='Creating a board office without its constitutional citation')
        self.assertEqual(error.code,"SR_DESIGNATION_INCOMPLETE", msg='Creating a board office without its constitutional citation should refuse because the designation is incomplete; another refusal was returned.')
        self.assertEqual(str(error),'This board office could not be recorded. A board office must cite the provision of your constitution that creates it, and which version; this designation does not. No office has been created or changed. Add the citation and submit again — or, if no provision creates this office, record it as a custom role.', msg="Explaining a board office's missing constitutional citation should ask for that citation or a custom role instead; the approved guidance differs.")
        self._unchanged_except(before,"CommandIntake", action_description='Refusing the board-office designation without its constitutional citation')

        uncited = self._command()
        self._class_c_refusal(replace(uncited,content=replace(uncited.content,participation_required=True)),
            "SR_GOVERNANCE_RELATIONSHIP_UNCITED",'This office could not be recorded. Each relationship between this office and your GOVERNANCE Circle — who elects or appoints to it, whether holding it requires GOVERNANCE participation, and any seat it carries — must cite the provision of your constitution that provides it, and which version; this designation leaves one uncited. No office has been created or changed. Add the citation, or remove the relationship, and submit again.', action_description='Creating a board office whose GOVERNANCE relationship lacks a citation')
        _, custom = self._define_custom()
        self._fixture_holder(custom, action_description='Appointing a consenting nominee to the custom-role fixture')
        self._class_c_refusal(self._command("RETIRE",row=custom),"SR_RETIREMENT_HOLDER_REMAINS","This office still has a holder. End or reassign the appointment through its own route first; retiring the definition does not end anyone's office. Nothing has changed.", action_description='Trying to retire a custom role that still has a holder')

        board_command, board = self._define()
        before = self._snapshot()
        with patch.object(self.service,"_evaluate_authority",wraps=self.service._evaluate_authority) as evaluate:
            error = self._failure(self._command("RETIRE",row=board),definitions.S015RoleDefinitionRefused, action_description='Trying to retire the permanent board office')
        self.assertFalse(evaluate.called, msg='Refusing board-office retirement should happen before an authority decision is requested; the service asked for a decision.')
        self.assertEqual(error.code,"SR_BOARD_OFFICE_PERMANENT", msg='Retiring a board office should refuse because board offices are permanent; another refusal was returned.')
        self.assertEqual(str(error),"Board offices cannot be retired. A board office is part of this organisation's governing structure, and its history is kept. If no one holds it, it simply remains vacant. No changes have been made to the office.", msg='Explaining refused board-office retirement should say that the office remains part of the governing structure and may remain vacant; the approved explanation differs.')
        self.assertEqual(error.support_class,"B", msg='Explaining that a board office cannot be retired should use support class B; another class was returned.')
        self._unchanged_except(before,"CommandIntake", action_description='Refusing retirement of the permanent board office')
        board.refresh_from_db()
        self.assertTrue(board.active, msg='Refusing to retire the board office should leave its definition active; the office became inactive.')
        self._provider_refusal(board_command.content,"C_BOARD_OFFICE_PERMANENT",action="RETIRE",row=board, action_description='Asking the provider to retire the permanent board office')
        with patch.object(self.service,"_evaluate_authority",wraps=self.service._evaluate_authority) as evaluate:
            self._class_c_refusal(self._command("AMEND",row=board,content=replace(board_command.content,
                kind="CUSTOM",nomination_basis="CUSTOM",provisions=())),
                "SR_DEFINITION_KIND_FIXED",'You cannot change this office from a board office to a custom role, or from a custom role to a board office. An office keeps the kind it was recorded with, and its history is kept. No changes have been made to the office.', action_description='Trying to turn the permanent board office into a custom role')
        self.assertFalse(evaluate.called, msg='Refusing to change a board office into a custom role should happen before an authority decision is requested; the service asked for a decision.')
        board.refresh_from_db()
        self.assertTrue(board.active, msg='Refusing to turn the board office into a custom role should leave its definition active; the office became inactive.')
        self._provider_refusal(replace(board_command.content,kind="CUSTOM",nomination_basis="CUSTOM",provisions=()),
            "C_DEFINITION_KIND_FIXED",action="AMEND",row=board, action_description='Asking the provider to change the board office into a custom role')
        self._fixture_holder(board, action_description='Appointing a consenting nominee to the board-office fixture')
        vacant_command, vacant = self._define()
        before = self._snapshot()
        observations = {r.role_identity:r for r in definitions.board_office_vacancies(self.organism.organism_id)}
        self.assertEqual(self._snapshot(),before, msg='Observing board-office vacancies should not write any records; the stored snapshot changed.')
        self.assertEqual(set(observations),{board.role_uuid,vacant.role_uuid}, msg='Observing board-office vacancies should return the occupied and vacant board offices and no others; the returned offices differ.')
        self.assertTrue(observations[board.role_uuid].filled, msg='Observing the occupied board office should show it as filled; it was shown as vacant.')
        self.assertFalse(observations[vacant.role_uuid].filled, msg='Observing the vacant board office should show it as vacant; it was shown as filled.')
        self.assertEqual((observations[vacant.role_uuid].code,observations[vacant.role_uuid].office_basis_reference,
            observations[vacant.role_uuid].office_basis_version),
            (vacant.code,vacant_command.content.provisions[0].provision_reference,vacant_command.content.provisions[0].provision_version), msg='Observing the vacant board office should preserve its recorded identity and constitutional citation/version; those details differ.')

        coordinator = models.OrganismRoleDefinition.objects.get(pk=self.ids["roles"]["COORDINATOR"])
        member = models.OrganismMembership.objects.select_related("head_transition").get(pk=self.ids["membership"])
        office = models.ContextualRoleAssignment.objects.select_related("head_state_event").get(
            membership=member,role_definition=coordinator)
        # F0 carries a pre-SR-shaped founding row. Submit valid new content,
        # not its nullable legacy fields, so AMEND reaches the reserved-code guard.
        content = definitions.DefinitionContent(code=coordinator.code,kind="COORDINATOR")
        for action in ("DEFINE","AMEND","RETIRE"):
            with self.subTest(coordinator_action=action):
                command = self._command(action,row=coordinator if action != "DEFINE" else None,content=content if action != "RETIRE" else None)
                before = self._snapshot()
                # A pre-decision refusal must not even consult authority.
                with patch.object(self.service,"_evaluate_authority",wraps=self.service._evaluate_authority) as evaluate:
                    error = self._failure(command,definitions.S015RoleDefinitionRefused, action_description='Trying to create, change or retire the founding Coordinator office')
                self.assertFalse(evaluate.called, msg='Refusing to create, change or retire Coordinator here should happen before an authority decision is requested; the service asked for a decision.')
                self.assertEqual(error.code,"SR_COORDINATOR_DEFINITION_RESERVED", msg='Creating, changing or retiring Coordinator here should refuse because it is established at founding; another refusal was returned.')
                self.assertEqual(str(error),'You cannot create, change or retire the Coordinator office here. This office is established when the Living Organism is founded. No changes have been made to the office.', msg="Explaining the reserved Coordinator definition should use the Human Governor's exact wording, including that it cannot be created here; the message differs.")
                self._unchanged_except(before,"CommandIntake", action_description='Refusing changes to the founding Coordinator definition')
                coordinator.refresh_from_db()
                self.assertEqual((coordinator.definition_version,coordinator.active),(1,True), msg='Refusing changes to Coordinator should leave its founding definition active at version one; its version or active status changed.')
                at = database_now()
                target = {"event_action":action}
                request = _authority_request(database_alias="default",actor=self.actor,actor_membership=member,
                    actor_role_head_refs=(office.head_state_event.lineage_reference,),
                    action=OrganismAction.RECORD_ROLE_DEFINITION,request_reference="c3-preserved:"+action,
                    idempotency_key="c3-preserved:"+action,received_at=at,effective_at=at,
                    target=target,target_fingerprint=authority_target_fingerprint(target),
                    living_organism_id=self.organism.organism_id)
                resolved = self.provider._effective_coordinator_office(request,member)
                self.assertIsNotNone(resolved, msg='Looking up the effective Coordinator after a refused definition change should still find the founding office; no office was found.')
                self.assertEqual(resolved.pk,office.pk, msg='Looking up the effective Coordinator after a refused definition change should return the same founding office; another appointment was returned.')

    def test_07_sr30_reached_at_real_command_commit(self):
        """O-22: deliberately bypass only local citation validation; sr_30 must refuse."""
        command = self._command()
        command = replace(command,content=replace(command.content,provisions=()))
        before = self._snapshot()
        with patch.object(definitions,"_check_citations",return_value=None):
            error = self._failure(command,definitions.S015RoleDefinitionGuardRefused)
        self.assertEqual(error.primary,"SR definition refused: office basis citation missing")
        self._unchanged_except(before,"CommandIntake")

    def test_08_sr20_reached_at_real_command_commit(self):
        """O-22: omit successor write after real deactivation; sr_20 must refuse at exit."""
        command, row = self._define()
        before = self._snapshot()
        amend = self._command("AMEND",row=row,content=command.content)
        with patch.object(definitions,"_write_definition_version",return_value=row):
            error = self._failure(amend,definitions.S015RoleDefinitionGuardRefused)
        self.assertEqual(error.primary,"SR definition refused: a deactivated version must be superseded by a successor at commit")
        self._unchanged_except(before,"CommandIntake")

    def test_09_completed_retry_and_changed_content_and_malformed_receipt(self):
        """O-29: exact retry has no new effect; altered request and corrupt result refuse."""
        command, row = self._define()
        before = self._snapshot()
        self.assertEqual(self._success(command).role_definition_uuid,row.role_uuid)
        self.assertEqual(self._snapshot(),before)
        self._failure(replace(command,content=replace(command.content,participation_required=True)),S015CommandReplayMismatch)
        self.assertEqual(self._snapshot(),before)
        original = self.service._replay_result
        def corrupt(payload):
            return original({**payload,"definition_version":True})
        with patch.object(self.service,"_replay_result",side_effect=corrupt):
            self._failure(command,S015CommandReceiptMalformed)
        self.assertEqual(self._snapshot(),before)

    def test_10_real_founding_still_records_c1_definition(self):
        """O-13/O-22 regression: real registration + founding, labelled fixture principal."""
        founder = register_test_learner(username="src3-found-" + self.tag)
        with transaction.atomic(),connection.cursor() as cursor:
            seed_fixture_substitute_authority(cursor,"src3-principal-" + self.tag)
        now = database_now()
        terms = registration_terms_version_in_force(effective_at=now,received_at=now)
        try:
            result = OrganismFormationService(clock=database_now).found_living_organism(FoundLivingOrganismCommand(
                credential=founder.credential,name="C3 Found " + self.tag,purpose="C3 founding regression",
                request_reference="c3-found:" + self.tag,idempotency_key="c3-found:" + self.tag,
                accepted_terms_version_uuid=terms.terms_version_uuid))
        except (DatabaseError,S015AuthorityDenied,definitions.S015RoleDefinitionError) as exc:
            self.fail("expected real founding to commit; observed " + type(exc).__name__ + "; C3 must preserve the founding route")
        role = models.OrganismRoleDefinition.objects.get(living_organism__organism_id=result.living_organism_uuid,code="COORDINATOR")
        decision = models.AuthorityDecision.objects.get(decision_reference=role.authority_decision_reference)
        self.assertEqual((role.kind,role.definition_version,role.enterer_id),("COORDINATOR",1,founder.pk))
        self.assertEqual((role.nomination_basis,role.nomination_policy),("SILENT","C"))
        self.assertEqual((decision.action,decision.outcome),("FOUND_LIVING_ORGANISM","QUALIFIED"))
        self.assertEqual(decision.evaluated_at,role.received_at)
        self.assertIsNotNone(role.recorded_at)
        self.assertFalse(role.provisions.exists())
