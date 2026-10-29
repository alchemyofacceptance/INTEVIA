"""U-FACTS-1 witnesses: the declared S015 authority facts vocabulary.

Three groups. The table witnesses check core.organism_authority_facts
against its own invariants and against the recorded-action set frozen here
as a literal. The corpus witnesses replay the frozen corpus
(tests/fixtures/s015_authority_facts_corpus_v1.json, written by
scripts/freeze_s015_facts_corpus.py on the pre-change tree) through the
table-driven parser and prove today's facts are reproduced byte for byte,
except at the two ruled discontinuities (F2, F3), whose exact change is
proved fact by fact. The producer witnesses exercise the production
provider's encoder without a database. One database class proves the
provider boundary: undeclared facts fail as S015AuthorityUnavailable with
no decision row.

The 0037 witness pins the SQL literals of migration 0037 to the table by
reading the migration module's SQL text; it runs no SQL.
"""

import hashlib
import importlib
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

from django.db import transaction
from django.test import SimpleTestCase, TestCase

from core.models import AuthorityDecision, Identity
from core.organism_authority_facts import (
    CONSENT_U_RELATIONS,
    FACT_BEARING_KINDS,
    FOUNDING_KEYS,
    FactKind,
    FactsSelectionError,
    S015AuthorityFactsUndeclared,
    S015_AUTHORITY_FACTS,
    S015_AUTHORITY_FACTS_SCHEMA,
    S015_AUTHORITY_REFERENCE_MAX_LENGTH,
    S015_AUTHORITY_REFERENCE_PREFIX,
    S015_AUTHORITY_REFUSAL_KEY,
    Selector,
    election_target_form,
    role_event_form,
    select_entry,
    select_refusal,
    validate_authority_facts,
)
from core.organism_contract import OrganismAction
from src.intevia.services import organism_authority
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityMalformed,
    S015AuthorityRefusalCode,
    S015AuthorityUnavailable,
    S015OwnApplicantAuthorityRefusal,
    S015OwnApplicantAuthorityRequest,
    S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityRefusal,
    S015PostFoundingAuthorityRequest,
    S015PostFoundingAuthorityResponse,
    S015PreFoundingAuthorityRefusal,
    S015PreFoundingAuthorityRequest,
    _facts_read,
    _refusal_result,
    authority_target_fingerprint,
    canonical_json_bytes,
)
from tests.s015_0021_support import create_test_identity


CORPUS_PATH = Path(__file__).resolve().parent / "fixtures" / "s015_authority_facts_corpus_v1.json"

# The recorded-action set as it stood at 8006f8c (organism_authority.py L897-917),
# frozen here as a literal so that neither the table nor _APPLICATION_ACTIONS can
# drift without this witness noticing (A-1).
RECORDED_ACTIONS_8006F8C = frozenset({
    "FOUND_LIVING_ORGANISM",
    "TRANSITION_LIVING_ORGANISM",
    "SUBMIT_APPLICATION",
    "WITHDRAW_APPLICATION",
    "CLAIM_APPLICATION",
    "REASSIGN_APPLICATION",
    "DECIDE_APPLICATION",
    "EFFECT_APPLICATION_SETTINGS",
    "EFFECT_APPLICATION_QUESTION",
    "EFFECT_GOVERNANCE_CONFIGURATION",
    "EFFECT_ENTITLEMENT_ELECTION",
    "RECORD_ENTITLEMENT_GRANT",
    "TRANSITION_CONTEXTUAL_ROLE",
    "RECORD_OFFICE_CONSENT",
    "TRANSITION_MEMBERSHIP",
    "ADOPT_GOVERNING_INSTRUMENT",
    "CREATE_DORMANT_CIRCLE",
    "ADMIT_CIRCLE_PARTICIPANT",
    "REQUEST_GOVERNANCE_SEAT",
})

BASELINE_COMMIT = "8006f8c267d3c613b4db0ed736cb697a7aac3969"
BASELINE_PARSER_SOURCE_SHA256 = "6b6dd263ec7d597852ddc48d0d8a01265f00e0c0ea136c0e250f4b0093ba348a"

T = datetime(2026, 10, 12, tzinfo=timezone.utc)
U = {n: UUID(int=n) for n in range(1, 10)}


class _Principal:
    def __init__(self, principal_uuid):
        self.principal_uuid = principal_uuid


def _decode(value):
    """Rebuild the typed values a corpus spec encodes: {"$uuid": s} is a UUID."""
    if isinstance(value, dict):
        if set(value) == {"$uuid"}:
            return UUID(value["$uuid"])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def build_request(spec):
    """Build the request a corpus case describes. spec["type"] selects the
    carrier: "pre", "post" or "own". Every other field is a constructor
    argument after _decode; timestamps are ISO-8601 strings.
    """
    kind = spec["type"]
    fields = {key: _decode(value) for key, value in spec.items() if key != "type"}
    for name in ("evaluated_at", "effective_at"):
        if isinstance(fields.get(name), str):
            fields[name] = datetime.fromisoformat(fields[name])
    fields["action"] = OrganismAction(fields["action"])
    fields.setdefault("database_alias", "default")
    fields.setdefault("actor_pk", 1)
    fields.setdefault("actor_access_epoch", 0)
    fields.setdefault("request_reference", "request:fixture")
    fields.setdefault("idempotency_key", "fixture")
    fields.setdefault("evaluated_at", T)
    if "target_fingerprint" not in fields:
        fields["target_fingerprint"] = authority_target_fingerprint(fields.get("target") or {})
    if kind == "pre":
        return S015PreFoundingAuthorityRequest(**fields)
    if kind == "own":
        fields.setdefault("actor_credential_pk", 1)
        fields.setdefault("living_organism_pk", 1)
        fields.setdefault("application_pk", None)
        return S015OwnApplicantAuthorityRequest(**fields)
    fields.setdefault("active_membership_head_reference", "membership-head:fixture")
    fields.setdefault("contextual_role_head_references", ())
    return S015PostFoundingAuthorityRequest(**fields)


_DEFAULT = object()


def _post(action, target=_DEFAULT, **overrides):
    organism_uuid = U[2]
    if target is _DEFAULT:
        target = {"action": action.value, "organism_uuid": str(organism_uuid)}
    fields = dict(
        database_alias="default",
        actor_pk=1,
        actor_identity_id=U[1],
        actor_access_epoch=0,
        action=action,
        command_subtype=action.value,
        target_fingerprint=authority_target_fingerprint(target) if target is not None else "0" * 64,
        request_reference="request:fixture",
        idempotency_key="fixture",
        evaluated_at=T,
        active_membership_head_reference="membership-head:fixture",
        contextual_role_head_references=(),
        target=target,
        living_organism_id=organism_uuid,
    )
    fields.update(overrides)
    return S015PostFoundingAuthorityRequest(**fields)


def _own(action, **overrides):
    target = {"action": action.value, "organism_uuid": str(U[2]), "applicant_identity_uuid": str(U[1])}
    fields = dict(
        database_alias="default",
        actor_pk=1,
        actor_identity_id=U[1],
        actor_credential_pk=1,
        actor_access_epoch=0,
        action=action,
        command_subtype=action.value,
        target_fingerprint=authority_target_fingerprint(target),
        request_reference="request:fixture",
        idempotency_key="fixture",
        evaluated_at=T,
        living_organism_pk=1,
        application_pk=None,
        target=target,
        living_organism_id=U[2],
    )
    fields.update(overrides)
    return S015OwnApplicantAuthorityRequest(**fields)


def _pre(action=OrganismAction.FOUND_LIVING_ORGANISM, **overrides):
    target = {
        "action": action.value,
        "founder_identity_uuid": U[1],
        "proposed_organism_uuid": U[2],
        "founding_rule_reference": "platform-terms-version:fixture",
        "instrument_references": [],
    }
    fields = dict(
        database_alias="default",
        actor_pk=1,
        actor_identity_id=U[1],
        actor_access_epoch=0,
        action=action,
        command_subtype=action.value,
        target_fingerprint=authority_target_fingerprint(target),
        request_reference="request:fixture",
        idempotency_key="fixture",
        evaluated_at=T,
        effective_at=T,
        target=target,
        living_organism_id=U[2],
    )
    fields.update(overrides)
    return S015PreFoundingAuthorityRequest(**fields)


def _reference(content):
    return S015_AUTHORITY_REFERENCE_PREFIX + canonical_json_bytes(content).decode("utf-8")


def _relations(facts):
    return [(fact["relation"], fact["reference"]) for fact in facts]


class S015AuthorityFactsTableTests(SimpleTestCase):
    """The table's own invariants and its agreement with the recorded-action set."""

    def test_table_covers_exactly_the_recorded_actions(self):
        declared = {action.value for action in S015_AUTHORITY_FACTS}
        self.assertEqual(declared, RECORDED_ACTIONS_8006F8C)
        self.assertEqual(
            {action.value for action in organism_authority._APPLICATION_ACTIONS},
            RECORDED_ACTIONS_8006F8C,
        )

    def test_every_fact_bearing_key_names_a_relation_and_descriptive_keys_do_not(self):
        for action, vocabulary in S015_AUTHORITY_FACTS.items():
            shapes = [(f"{action.value} entry {entry.form}/{dict(entry.discriminators)}", entry.keys) for entry in vocabulary.entries]
            shapes += [(f"{action.value} refusal {form}", shape.keys) for form, shape in vocabulary.refusals.items()]
            for label, keys in shapes:
                for key, spec in keys.items():
                    with self.subTest(shape=label, key=key):
                        if spec.kind in FACT_BEARING_KINDS:
                            self.assertTrue(spec.relation, "fact-bearing key with no relation")
                        elif spec.kind is FactKind.CONSENT_U:
                            self.assertIsNone(spec.relation)
                            self.assertTrue(all(CONSENT_U_RELATIONS.values()))
                        else:
                            self.assertIn(spec.kind, {FactKind.TEXT, FactKind.LITERAL})
                            self.assertIsNone(spec.relation)
                            self.assertIsNone(spec.prefix)
                        if spec.kind is FactKind.LITERAL:
                            self.assertTrue(spec.literals, "LITERAL key without literals")
                        self.assertNotIn(key, {S015_AUTHORITY_REFUSAL_KEY, "kind"})

    def test_discriminators_are_not_keys_and_entries_of_a_form_are_distinct(self):
        for action, vocabulary in S015_AUTHORITY_FACTS.items():
            seen = set()
            for entry in vocabulary.entries:
                with self.subTest(action=action.value, entry=dict(entry.discriminators)):
                    self.assertFalse(set(entry.discriminators) & set(entry.keys))
                    signature = (entry.form, tuple(sorted(entry.discriminators.items())))
                    self.assertNotIn(signature, seen)
                    seen.add(signature)
                    for group in entry.exactly_one:
                        self.assertTrue(group <= set(entry.keys))
                    for key, by_literal in entry.required_when.items():
                        self.assertIn(key, entry.keys)
                        for needed in by_literal.values():
                            self.assertTrue(needed <= set(entry.keys))
                    if entry.founding_block:
                        self.assertTrue(FOUNDING_KEYS <= set(entry.keys))
            if vocabulary.selector is Selector.NONE:
                self.assertTrue(all(entry.form is None for entry in vocabulary.entries))
                self.assertEqual(set(vocabulary.refusals), {None})

    def test_nested_mappings_are_frozen(self):
        self.assertIsInstance(S015_AUTHORITY_FACTS, MappingProxyType)
        for vocabulary in S015_AUTHORITY_FACTS.values():
            self.assertIsInstance(vocabulary.refusals, MappingProxyType)
            for entry in vocabulary.entries:
                self.assertIsInstance(entry.discriminators, MappingProxyType)
                self.assertIsInstance(entry.keys, MappingProxyType)
                self.assertIsInstance(entry.required_when, MappingProxyType)
            for shape in vocabulary.refusals.values():
                self.assertIsInstance(shape.keys, MappingProxyType)

    def test_o_is_a_refusal_only_key_on_consent_and_membership_rows_only(self):
        refusal_only = set()
        for action, vocabulary in S015_AUTHORITY_FACTS.items():
            qualified = set().union(*(set(entry.keys) for entry in vocabulary.entries))
            refused = set().union(*(set(shape.keys) for shape in vocabulary.refusals.values()))
            if "o" in refused - qualified:
                refusal_only.add(action)
        self.assertEqual(refusal_only, {OrganismAction.RECORD_OFFICE_CONSENT, OrganismAction.TRANSITION_MEMBERSHIP})

    def test_submit_row_declares_the_ruled_labels_and_the_compatibility_key(self):
        entry = S015_AUTHORITY_FACTS[OrganismAction.SUBMIT_APPLICATION].entries[0]
        self.assertEqual((entry.keys["r"].relation, entry.keys["r"].prefix), ("learner-registration", "learner-registration:"))
        self.assertFalse(entry.keys["p"].required)
        self.assertEqual(entry.keys["p"].relation, "core_application")
        for action in (OrganismAction.TRANSITION_LIVING_ORGANISM, OrganismAction.EFFECT_ENTITLEMENT_ELECTION):
            for form, shape in S015_AUTHORITY_FACTS[action].refusals.items():
                with self.subTest(action=action.value, form=form):
                    if not shape.exact:
                        self.assertIs(shape.keys["p"].kind, FactKind.TEXT)

    def test_transition_role_refusal_shape_declares_no_g(self):
        shape = S015_AUTHORITY_FACTS[OrganismAction.TRANSITION_CONTEXTUAL_ROLE].refusals["TRANSITION"]
        self.assertNotIn("g", shape.keys)
        self.assertEqual(set(shape.keys), {"a", "k", "o", "p", "u"})


class S015AuthorityFactsSelectorTests(SimpleTestCase):
    def test_election_target_forms(self):
        action = OrganismAction.EFFECT_ENTITLEMENT_ELECTION
        snapshot = _post(action, target={"action": action.value, "organism_uuid": U[2], "snapshot_uuid": str(U[6])}, version_uuid=U[7], version_kind="EFFECT")
        gateway = _post(action, target={"action": action.value, "organism_uuid": U[2], "version_uuid": U[8]}, version_uuid=U[8], version_kind="EFFECT")
        self.assertEqual(election_target_form(snapshot), "SNAPSHOT")
        self.assertEqual(election_target_form(gateway), "GATEWAY")
        self.assertIsNone(election_target_form(replace(snapshot, version_kind="DRAFT")))
        self.assertIsNone(election_target_form(replace(snapshot, version_uuid=None)))
        self.assertIsNone(election_target_form(replace(gateway, version_uuid=U[9])))
        self.assertIsNone(election_target_form(_post(action, target={"action": action.value, "organism_uuid": U[2], "version_uuid": str(U[8])}, version_uuid=U[8], version_kind="EFFECT")))
        self.assertIsNone(election_target_form(_post(action)))

    def test_role_event_forms(self):
        action = OrganismAction.TRANSITION_CONTEXTUAL_ROLE
        self.assertEqual(role_event_form(_post(action, target={"action": action.value, "event_action": "PROPOSE"})), "PROPOSE")
        self.assertEqual(role_event_form(_post(action, target={"action": action.value, "event_action": "ASSIGN"})), "TRANSITION")
        self.assertEqual(role_event_form(_post(action, target=None)), "TRANSITION")

    def test_select_entry_requires_exactly_one_match(self):
        admit = S015_AUTHORITY_FACTS[OrganismAction.ADMIT_CIRCLE_PARTICIPANT]
        request = _post(OrganismAction.ADMIT_CIRCLE_PARTICIPANT)
        self.assertEqual(dict(select_entry(admit, request, {"kind": "GOVERNANCE", "route": "CIRCLE_FOUNDING"}).discriminators), {"kind": "GOVERNANCE", "route": "CIRCLE_FOUNDING"})
        for content in ({}, {"kind": "GOVERNANCE"}, {"kind": "GOVERNANCE", "route": "OTHER"}):
            with self.subTest(content=content):
                with self.assertRaises(FactsSelectionError):
                    select_entry(admit, request, content)
        election = S015_AUTHORITY_FACTS[OrganismAction.EFFECT_ENTITLEMENT_ELECTION]
        neither = _post(OrganismAction.EFFECT_ENTITLEMENT_ELECTION)
        self.assertEqual(set(select_refusal(election, neither).keys), {"p"})
        self.assertFalse(select_refusal(election, neither).exact)


class S015AuthorityFactsCorpusTests(SimpleTestCase):
    """Replay of the frozen corpus through the table-driven parser."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    def test_corpus_is_pinned_to_the_baseline(self):
        corpus = self.corpus
        self.assertEqual(corpus["schema"], S015_AUTHORITY_FACTS_SCHEMA)
        self.assertEqual(corpus["baseline_commit"], BASELINE_COMMIT)
        self.assertEqual(corpus["baseline_parser_source_sha256"], BASELINE_PARSER_SOURCE_SHA256)
        self.assertRegex(corpus["generator_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(corpus["authorised_by"], r"^[0-9a-f]{64}$")
        cases = corpus["cases"]
        self.assertEqual(len(cases), len({case["id"] for case in cases}))
        digest = hashlib.sha256(json.dumps(cases, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(corpus["cases_sha256"], digest)
        self.assertEqual({case["group"] for case in cases} - {"PP", "PC", "NEG"}, set())

    def _run(self, case):
        request = build_request(case["request"])
        principal = _Principal(UUID(case["principal"])) if case.get("principal") else None
        return request, _facts_read(request, case["reference"], principal)

    def test_every_accepted_case_reproduces_its_expected_facts(self):
        for case in self.corpus["cases"]:
            if case["group"] == "NEG":
                continue
            with self.subTest(case=case["id"]):
                request, facts = self._run(case)
                self.assertEqual(canonical_json_bytes(facts).decode("utf-8"), case["expected_after"])
                for fact in facts:
                    self.assertEqual(set(fact), {"relation", "reference", "eligible_at"})
                    self.assertTrue(fact["relation"])

    def test_discontinuities_change_exactly_the_ruled_fact(self):
        for case in self.corpus["cases"]:
            if case["group"] == "NEG":
                continue
            with self.subTest(case=case["id"]):
                before = json.loads(case["expected_before"])
                after = json.loads(case["expected_after"])
                change = case.get("discontinuity")
                if change is None:
                    self.assertEqual(before, after)
                    continue
                if change == "F2":
                    self.assertEqual(len(before), len(after))
                    differing = [index for index, (b, a) in enumerate(zip(before, after)) if b != a]
                    self.assertEqual(len(differing), 1)
                    index = differing[0]
                    self.assertEqual(before[index]["relation"], "core_identitytransition")
                    self.assertTrue(before[index]["reference"].startswith("identity-transition:"))
                    self.assertEqual(after[index]["relation"], "learner-registration")
                    self.assertEqual(after[index]["reference"], "learner-registration:" + before[index]["reference"][len("identity-transition:"):])
                    self.assertEqual(before[index]["eligible_at"], after[index]["eligible_at"])
                elif change == "F3":
                    self.assertEqual(len(before), len(after) + 1)
                    removed = [fact for fact in before if fact not in after]
                    self.assertEqual(len(removed), 1)
                    self.assertEqual(removed[0]["relation"], "core_application")
                    self.assertEqual(removed[0]["reference"], json.loads(case["reference"][len(S015_AUTHORITY_REFERENCE_PREFIX):])["p"])
                    self.assertEqual([fact for fact in before if fact is not removed[0]], after)
                else:
                    self.fail(f"unknown discontinuity {change!r}")

    def test_production_producer_cases_pass_the_encoder(self):
        for case in self.corpus["cases"]:
            if case["group"] != "PP":
                continue
            with self.subTest(case=case["id"]):
                request = build_request(case["request"])
                content = json.loads(case["reference"][len(S015_AUTHORITY_REFERENCE_PREFIX):])
                validate_authority_facts(request, content, refusal_code=content.get(S015_AUTHORITY_REFUSAL_KEY))

    def test_negative_cases_are_refused_by_the_parser(self):
        for case in self.corpus["cases"]:
            if case["group"] != "NEG":
                continue
            with self.subTest(case=case["id"]):
                request = build_request(case["request"])
                principal = _Principal(UUID(case["principal"])) if case.get("principal") else None
                with self.assertRaises(S015AuthorityMalformed) as caught:
                    _facts_read(request, case["reference"], principal)
                if case.get("message"):
                    self.assertEqual(str(caught.exception), case["message"])


class S015AuthorityFactsParserBoundaryTests(SimpleTestCase):
    """Parser boundaries not expressible as a stored corpus string."""

    def test_unstructured_reference_yields_the_base_facts_only(self):
        request = _post(OrganismAction.RECORD_ENTITLEMENT_GRANT)
        facts = _facts_read(request, "authority:fixture")
        self.assertEqual([fact["relation"] for fact in facts], ["core_identity", "core_livingorganism"])
        self.assertEqual(len(_facts_read(request, None)), 2)
        with self.assertRaises(S015AuthorityMalformed):
            _facts_read(_post(OrganismAction.EFFECT_ENTITLEMENT_ELECTION), None)

    def test_refusal_result_shape_parses_for_every_request_type(self):
        for request in (_pre(), _own(OrganismAction.WITHDRAW_APPLICATION, application_pk=1), _post(OrganismAction.REQUEST_GOVERNANCE_SEAT)):
            with self.subTest(type=type(request).__name__):
                refusal = _refusal_result(request, S015AuthorityRefusalCode.DENIED, S015AuthorityRefusalCode.DENIED)
                self.assertEqual(refusal.authority_reference, 's015a1:{"x":"DENIED"}')
                self.assertEqual(len(_facts_read(request, refusal.authority_reference)), 2)

    def test_propose_policy_value_must_be_a_string_and_may_be_absent(self):
        """A-2 (a): a PROPOSE reference whose k is an array or an object is
        S015AuthorityMalformed (the POLICY_K value check), never a TypeError
        from the policy-letter membership test; a reference without k keeps
        its compatible reading (no role fact, no policy fact). Kept outside
        the frozen corpus, whose generator catches only Malformed."""
        action = OrganismAction.TRANSITION_CONTEXTUAL_ROLE
        request = _post(action, target={"action": action.value, "event_action": "PROPOSE", "role_definition_uuid": str(U[9])})
        content = {"t": "SELF_PROPOSAL", "k": "A", "i": None, "n": str(U[4]), "m": str(U[3]), "c": str(U[5])}
        for label, bad in (("array", []), ("object", {}), ("array of letters", ["A"]), ("object with k", {"k": "A"})):
            with self.subTest(k=label):
                with self.assertRaises(S015AuthorityMalformed) as caught:
                    _facts_read(request, _reference({**content, "k": bad}))
                self.assertEqual(str(caught.exception), "structured authority reference has an invalid fact value")
        # _reference is canonical (keys sorted: c, i, m, n, t), so the facts follow that order
        without_k = _facts_read(request, _reference({key: value for key, value in content.items() if key != "k"}))
        self.assertEqual(_relations(without_k), [
            ("core_identity", str(U[1])),
            ("core_livingorganism", str(U[2])),
            ("core_logovernanceconfigurationversion", "governance-configuration-version:" + str(U[5])),
            ("s2b_coordinator_nomination_entitlement", None),
            ("core_organismmembershiptransition", str(U[3])),
            ("core_organismmembershiptransition", str(U[4])),
        ])

    def test_fact_order_is_the_reference_key_order_after_the_derived_block(self):
        action = OrganismAction.TRANSITION_CONTEXTUAL_ROLE
        request = _post(action, target={"action": action.value, "event_action": "PROPOSE", "role_definition_uuid": str(U[9])})
        reference = S015_AUTHORITY_REFERENCE_PREFIX + json.dumps({"t": "SELF_PROPOSAL", "k": "A", "i": None, "n": str(U[4]), "m": str(U[3]), "c": str(U[5])}, separators=(",", ":"))
        facts = _facts_read(request, reference)
        self.assertEqual(_relations(facts), [
            ("core_identity", str(U[1])),
            ("core_livingorganism", str(U[2])),
            ("core_organismroledefinition", str(U[9])),
            ("s2b_coordinator_nomination_policy", "A"),
            ("s2b_coordinator_nomination_entitlement", None),
            ("core_organismmembershiptransition", str(U[4])),
            ("core_organismmembershiptransition", str(U[3])),
            ("core_logovernanceconfigurationversion", "governance-configuration-version:" + str(U[5])),
        ])


class S015AuthorityFactsProducerTests(SimpleTestCase):
    """The production provider's encoder, without a database."""

    def _reassign_request(self):
        action = OrganismAction.REASSIGN_APPLICATION
        return _post(action, target={"action": action.value, "organism_uuid": str(U[2]), "application_uuid": str(U[8])})

    def _lawful_reassign_facts(self, **overrides):
        facts = {"a": str(U[3]), "c": str(U[4]), "e": 3, "m": str(U[5]), "o": str(U[6]), "s": str(U[7])}
        facts.update(overrides)
        return facts

    def test_encode_facts_returns_a_validated_string(self):
        request = self._reassign_request()
        reference = ProductionApplicationAuthorityProvider._encode_facts(request, self._lawful_reassign_facts(), refusal_code=None)
        self.assertIsInstance(reference, str)
        self.assertTrue(reference.startswith(S015_AUTHORITY_REFERENCE_PREFIX))
        self.assertEqual(json.loads(reference[len(S015_AUTHORITY_REFERENCE_PREFIX):]), self._lawful_reassign_facts())

    def test_qualified_refuses_undeclared_facts_before_any_size_check(self):
        request = self._reassign_request()
        for label, facts in (
            ("x on a qualified output", self._lawful_reassign_facts(x="DENIED")),
            ("undeclared short key", self._lawful_reassign_facts(zz="1")),
            ("undeclared oversized", self._lawful_reassign_facts(zz="z" * 300)),
            ("missing required", {"a": str(U[3]), "c": str(U[4]), "e": 3}),
            ("null on REF", self._lawful_reassign_facts(c=None)),
            ("wrong kind", self._lawful_reassign_facts(e=1.5)),
            ("unknown literal", {**self._lawful_reassign_facts(), "t": "LATER"}),
        ):
            with self.subTest(case=label):
                with self.assertRaises(S015AuthorityFactsUndeclared) as caught:
                    ProductionApplicationAuthorityProvider._qualified(request, facts)
                self.assertIsInstance(caught.exception, ValueError)
                self.assertIn(S015_AUTHORITY_FACTS_SCHEMA, str(caught.exception))

    def test_lawful_oversized_qualified_output_is_the_size_refusal(self):
        request = self._reassign_request()
        result = ProductionApplicationAuthorityProvider._qualified(request, self._lawful_reassign_facts(a="a" * 220))
        self.assertIs(type(result), S015PostFoundingAuthorityRefusal)
        self.assertIs(result.refusal_code, S015AuthorityRefusalCode.C_REFERENCE_UNREPRESENTABLE)
        self.assertIs(result.disclosed_refusal_code, S015AuthorityRefusalCode.DENIED)
        self.assertEqual(result.authority_reference, 's015a1:{"x":"C_REFERENCE_UNREPRESENTABLE"}')
        self.assertEqual(len(result.authority_reference), 42)
        normal = ProductionApplicationAuthorityProvider._qualified(request, self._lawful_reassign_facts())
        self.assertIs(type(normal), S015PostFoundingAuthorityResponse)
        self.assertLessEqual(len(normal.authority_reference), S015_AUTHORITY_REFERENCE_MAX_LENGTH)

    def test_size_refusal_carries_every_request_type(self):
        for request, expected in (
            (_pre(), S015PreFoundingAuthorityRefusal),
            (_own(OrganismAction.SUBMIT_APPLICATION), S015OwnApplicantAuthorityRefusal),
            (self._reassign_request(), S015PostFoundingAuthorityRefusal),
        ):
            with self.subTest(type=expected.__name__):
                result = ProductionApplicationAuthorityProvider._refusal_for_size(request)
                self.assertIs(type(result), expected)
                self.assertEqual(result.authority_reference, 's015a1:{"x":"C_REFERENCE_UNREPRESENTABLE"}')

    def test_refuse_keeps_its_code_when_the_auxiliary_facts_are_oversized(self):
        provider = ProductionApplicationAuthorityProvider()
        action = OrganismAction.TRANSITION_MEMBERSHIP
        request = _post(action, target={"action": action.value, "organism_uuid": str(U[2]), "resulting_state": "ENDED"})
        code = S015AuthorityRefusalCode.C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED
        disclosed = S015AuthorityRefusalCode.DENIED
        self.assertIsNot(code, disclosed)
        normal = provider._refuse(request, code, disclosed, facts={"o": str(U[3])})
        self.assertIs(type(normal), S015PostFoundingAuthorityRefusal)
        self.assertIs(normal.refusal_code, code)
        self.assertIs(normal.disclosed_refusal_code, disclosed)
        self.assertEqual(json.loads(normal.authority_reference[len("s015a1:"):]), {"x": code.value, "o": str(U[3])})
        oversized = provider._refuse(request, code, disclosed, facts={"o": "o" * 300})
        self.assertIs(oversized.refusal_code, code)
        self.assertIs(oversized.disclosed_refusal_code, disclosed)
        self.assertEqual(oversized.authority_reference, 's015a1:{"x":"C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED"}')
        self.assertEqual(json.loads(oversized.authority_reference[len("s015a1:"):])["x"], code.value)
        self.assertEqual(len(oversized.authority_reference), 61)
        self.assertEqual(len(_facts_read(request, oversized.authority_reference)), 2)

    def test_refuse_refuses_undeclared_auxiliary_facts_and_an_x_override(self):
        provider = ProductionApplicationAuthorityProvider()
        action = OrganismAction.TRANSITION_MEMBERSHIP
        request = _post(action, target={"action": action.value, "organism_uuid": str(U[2]), "resulting_state": "ENDED"})
        code = S015AuthorityRefusalCode.DENIED
        for label, facts in (
            ("x override", {"x": "OTHER"}),
            ("undeclared short key", {"zz": "1"}),
            ("undeclared oversized key", {"zz": "z" * 300}),
            ("unlawful value", {"o": None}),
        ):
            with self.subTest(case=label):
                with self.assertRaises(S015AuthorityFactsUndeclared):
                    provider._refuse(request, code, code, facts=facts)
        plain = provider._refuse(request, code, code)
        self.assertEqual(plain.authority_reference, 's015a1:{"x":"DENIED"}')

    def test_refuse_covers_the_pre_founding_and_own_applicant_carriers(self):
        provider = ProductionApplicationAuthorityProvider()
        pre = provider._refuse(_pre(), S015AuthorityRefusalCode.UNRECOGNISED_ACTION, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        self.assertIs(type(pre), S015PreFoundingAuthorityRefusal)
        own = provider._refuse(_own(OrganismAction.SUBMIT_APPLICATION), S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN, S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN, facts={"b": None})
        self.assertIs(type(own), S015OwnApplicantAuthorityRefusal)
        self.assertEqual(own.authority_reference, 's015a1:{"b":null,"x":"C_GATEWAY_NOT_OPEN"}')

    def test_unrecorded_action_admits_an_x_only_refusal_and_nothing_else(self):
        provider = ProductionApplicationAuthorityProvider()
        request = _post(OrganismAction.RECORD_GOVERNED_DETERMINATION)
        code = S015AuthorityRefusalCode.UNRECOGNISED_ACTION
        refusal = provider._refuse(request, code, code)
        self.assertIs(type(refusal), S015PostFoundingAuthorityRefusal)
        self.assertEqual(refusal.authority_reference, 's015a1:{"x":"UNRECOGNISED_ACTION"}')
        with self.assertRaises(S015AuthorityFactsUndeclared):
            provider._refuse(request, code, code, facts={"o": str(U[3])})
        with self.assertRaises(S015AuthorityFactsUndeclared):
            ProductionApplicationAuthorityProvider._qualified(request, {})

    def test_sentinel_does_not_bypass_a_declared_literal_set(self):
        action = OrganismAction.TRANSITION_CONTEXTUAL_ROLE
        propose = _post(action, target={"action": action.value, "event_action": "PROPOSE", "role_definition_uuid": str(U[9])})
        lawful = {"m": "-", "n": "-", "t": "SELF_PROPOSAL", "k": "C", "i": None, "d": "C"}
        validate_authority_facts(propose, lawful, refusal_code=None)
        with self.assertRaises(S015AuthorityFactsUndeclared):
            validate_authority_facts(propose, {**lawful, "d": "-"}, refusal_code=None)

    def test_validator_refuses_non_string_keys_before_selecting_or_sorting(self):
        """A-2 (b): a facts mapping with a key that is not a str is
        S015AuthorityFactsUndeclared at the validator's entrance, on the
        qualified path with mixed undeclared key types (which the sorted
        error message could not order), on the refusal path, and for an
        action with no declared row."""
        request = _own(OrganismAction.SUBMIT_APPLICATION, predecessor_reference=U[8])
        lawful = {"b": "living-organism-event:" + str(U[3]), "c": str(U[4]), "q": str(U[5]), "r": str(U[6]), "s": str(U[7])}
        validate_authority_facts(request, lawful, refusal_code=None)
        for label, facts, refusal_code in (
            ("mixed undeclared int and str keys", {**lawful, 1: "x1", "zz": "1"}, None),
            ("int key alone", {**lawful, 1: "x1"}, None),
            ("tuple key", {**lawful, ("k",): "A"}, None),
            ("refusal path", {"x": "DENIED", 1: "x1"}, "DENIED"),
        ):
            with self.subTest(case=label):
                with self.assertRaises(S015AuthorityFactsUndeclared) as caught:
                    validate_authority_facts(request, facts, refusal_code=refusal_code)
                self.assertIn("facts carry a key that is not a string", str(caught.exception))
        unrecorded = _post(OrganismAction.RECORD_GOVERNED_DETERMINATION)
        with self.assertRaises(S015AuthorityFactsUndeclared) as caught:
            validate_authority_facts(unrecorded, {"x": "DENIED", 1: "x1"}, refusal_code="DENIED")
        self.assertIn("facts carry a key that is not a string", str(caught.exception))

    def test_submit_output_without_a_predecessor_key_is_lawful_and_within_the_cap(self):
        request = _own(OrganismAction.SUBMIT_APPLICATION, predecessor_reference=U[8])
        facts = {"b": "living-organism-event:" + str(U[3]), "c": str(U[4]), "q": str(U[5]), "r": str(U[6]), "s": str(U[7])}
        result = ProductionApplicationAuthorityProvider._qualified(request, facts)
        self.assertIs(type(result), S015OwnApplicantAuthorityResponse)
        self.assertEqual(len(result.authority_reference), 245)
        self.assertEqual(result.predecessor_reference, U[8])
        with_p = ProductionApplicationAuthorityProvider._qualified(request, {**facts, "p": str(U[8])})
        self.assertIs(type(with_p), S015OwnApplicantAuthorityRefusal)
        self.assertEqual(len(S015_AUTHORITY_REFERENCE_PREFIX + canonical_json_bytes({**facts, "p": str(U[8])}).decode("utf-8")), 288)

    def test_founding_values_must_have_their_exact_forms(self):
        request = _pre()
        lawful = {
            "kind": "FOUNDING",
            "rule": "platform-terms-version:" + str(U[3]) + "#founding",
            "registration": "learner-registration:" + str(U[4]),
            "terms": "platform-terms-acceptance:" + str(U[5]),
        }
        validate_authority_facts(request, lawful, refusal_code=None)
        self.assertEqual(len(_reference(lawful)), 252)
        for key, value in (("rule", str(U[3])), ("rule", "platform-terms-version:" + str(U[3])), ("registration", str(U[4])), ("terms", str(U[5]))):
            with self.subTest(key=key):
                with self.assertRaises(S015AuthorityFactsUndeclared):
                    validate_authority_facts(request, {**lawful, key: value}, refusal_code=None)
        with self.assertRaises(S015AuthorityFactsUndeclared):
            validate_authority_facts(request, {"kind": "FOUNDING"}, refusal_code=None)

    def test_propose_and_assign_invariants_bind_the_producer(self):
        action = OrganismAction.TRANSITION_CONTEXTUAL_ROLE
        propose = _post(action, target={"action": action.value, "event_action": "PROPOSE", "role_definition_uuid": str(U[9])})
        lawful = {"m": str(U[3]), "n": str(U[4]), "t": "MEMBER_NOMINATION", "k": "A", "i": None, "c": str(U[5])}
        validate_authority_facts(propose, lawful, refusal_code=None)
        for label, facts in (
            ("c and d", {**lawful, "d": "C"}),
            ("neither c nor d", {key: value for key, value in lawful.items() if key != "c"}),
            ("d literal", {**{key: value for key, value in lawful.items() if key != "c"}, "d": "X"}),
            ("k literal", {**lawful, "k": "Z"}),
        ):
            with self.subTest(case=label):
                with self.assertRaises(S015AuthorityFactsUndeclared):
                    validate_authority_facts(propose, facts, refusal_code=None)
        no_role = _post(action, target={"action": action.value, "event_action": "PROPOSE"})
        with self.assertRaises(S015AuthorityFactsUndeclared):
            validate_authority_facts(no_role, lawful, refusal_code=None)
        assign = _post(action, target={"action": action.value, "event_action": "ASSIGN", "assignment_uuid": str(U[3])})
        validate_authority_facts(assign, {"a": str(U[3]), "g": str(U[4]), "k": "ASSIGN", "p": str(U[5]), "u": "e:" + str(U[5])}, refusal_code=None)
        with self.assertRaises(S015AuthorityFactsUndeclared):
            validate_authority_facts(assign, {"a": str(U[3]), "g": str(U[4]), "k": "ASSIGN"}, refusal_code=None)
        validate_authority_facts(assign, {"a": str(U[3]), "g": str(U[4]), "k": "END"}, refusal_code=None)
        with self.assertRaises(S015AuthorityFactsUndeclared):
            validate_authority_facts(assign, {"x": "DENIED", "a": str(U[3]), "k": "ASSIGN", "g": str(U[4])}, refusal_code="DENIED")

    def test_election_gateway_refusal_admits_x_only(self):
        action = OrganismAction.EFFECT_ENTITLEMENT_ELECTION
        gateway = _post(action, target={"action": action.value, "organism_uuid": U[2], "version_uuid": U[8]}, version_uuid=U[8], version_kind="EFFECT")
        validate_authority_facts(gateway, {"x": "DENIED"}, refusal_code="DENIED")
        with self.assertRaises(S015AuthorityFactsUndeclared):
            validate_authority_facts(gateway, {"x": "DENIED", "p": "phase"}, refusal_code="DENIED")
        snapshot = _post(action, target={"action": action.value, "organism_uuid": U[2], "snapshot_uuid": str(U[6])}, version_uuid=U[7], version_kind="EFFECT")
        validate_authority_facts(snapshot, {"x": "BIRTHING_PREREQUISITE_MISSING", "p": "phase"}, refusal_code="BIRTHING_PREREQUISITE_MISSING")


class S015AuthorityFacts0037WitnessTests(SimpleTestCase):
    """The SQL literals of migration 0037, pinned to the table without a database."""

    def setUp(self):
        migration = importlib.import_module("core.migrations.0037_s2b_office_appointment_consent")
        self.consent_sql = migration.CONSENT_SQL
        self.assign_sql = migration.ASSIGN_CONSENT_SQL

    def test_prefix_literals(self):
        for sql in (self.consent_sql, self.assign_sql):
            self.assertIn("LIKE '" + S015_AUTHORITY_REFERENCE_PREFIX + "%'", sql)
            self.assertIn(f"substring(decision_row.authority_reference FROM {len(S015_AUTHORITY_REFERENCE_PREFIX) + 1})::jsonb", sql)

    def test_reference_keys_read_by_the_sql_are_declared(self):
        consent = S015_AUTHORITY_FACTS[OrganismAction.RECORD_OFFICE_CONSENT].entries[0].keys
        transition = S015_AUTHORITY_FACTS[OrganismAction.TRANSITION_CONTEXTUAL_ROLE].entries[1].keys
        self.assertEqual(S015_AUTHORITY_FACTS[OrganismAction.TRANSITION_CONTEXTUAL_ROLE].entries[1].form, "TRANSITION")
        for sql, keys in ((self.consent_sql, consent), (self.assign_sql, transition)):
            read = set(re.findall(r"reference_facts->>'([a-z]+)'", sql))
            self.assertEqual(read, {"a", "g", "p", "u"})
            self.assertTrue(read <= set(keys))
        self.assertIs(consent["u"].kind, FactKind.REF)
        self.assertEqual(consent["u"].relation, "core_organismmembership")
        self.assertIs(transition["u"].kind, FactKind.CONSENT_U)
        self.assertNotIn("g", S015_AUTHORITY_FACTS[OrganismAction.TRANSITION_CONTEXTUAL_ROLE].refusals["TRANSITION"].keys)

    def test_consent_u_prefixes_and_relations(self):
        self.assertIn("THEN 'c:' ELSE 'e:' END", self.assign_sql)
        self.assertEqual(set(CONSENT_U_RELATIONS), {"c:", "e:"})
        self.assertIn("consent_relation := 'core_officeappointmentconsent'", self.assign_sql)
        self.assertIn("consent_relation := 'core_contextualroleassignmentstateevent'", self.assign_sql)
        self.assertEqual(CONSENT_U_RELATIONS["c:"], "core_officeappointmentconsent")
        self.assertEqual(CONSENT_U_RELATIONS["e:"], "core_contextualroleassignmentstateevent")

    def test_containment_relations_match_the_declared_relations(self):
        consent = S015_AUTHORITY_FACTS[OrganismAction.RECORD_OFFICE_CONSENT].entries[0].keys
        transition = S015_AUTHORITY_FACTS[OrganismAction.TRANSITION_CONTEXTUAL_ROLE].entries[1].keys
        literals = set(re.findall(r"'relation', '([a-z_]+)'", self.consent_sql + self.assign_sql))
        self.assertEqual(literals, {
            "core_contextualroleassignment",
            "core_contextualroleassignmentstateevent",
            "core_organismmembership",
            "core_organismroledefinition",
        })
        self.assertEqual(consent["a"].relation, "core_contextualroleassignment")
        self.assertEqual(consent["p"].relation, "core_contextualroleassignmentstateevent")
        self.assertEqual(consent["g"].relation, "core_organismroledefinition")
        self.assertEqual(consent["u"].relation, "core_organismmembership")
        self.assertEqual(transition["a"].relation, "core_contextualroleassignment")
        self.assertEqual(transition["p"].relation, "core_contextualroleassignmentstateevent")
        self.assertEqual(transition["g"].relation, "core_organismroledefinition")
        self.assertIn("'relation', consent_relation, 'reference', consent_reference", self.assign_sql)


class _UndeclaredFactsProvider:
    provider_identity = "undeclared-facts-witness"
    provider_version = "1"

    def __init__(self):
        self.calls = 0

    def evaluate_organism_authority(self, request):
        self.calls += 1
        return ProductionApplicationAuthorityProvider._qualified(request, {"zz": "1"})


class S015AuthorityFactsBoundaryDatabaseTests(TestCase):
    """The provider boundary: undeclared facts fail closed with no row."""

    def test_undeclared_producer_facts_are_unavailable_and_write_no_row(self):
        identity = create_test_identity(
            username="s015_facts_boundary_witness",
            id=1,
            access_state=Identity.AccessState.ACTIVE,
        )
        request = _post(
            OrganismAction.RECORD_ENTITLEMENT_GRANT,
            actor_identity_id=identity.identity_id,
            actor_access_epoch=identity.access_epoch,
        )
        provider = _UndeclaredFactsProvider()
        before = AuthorityDecision.objects.count()
        with transaction.atomic():
            with self.assertRaises(S015AuthorityUnavailable) as caught:
                S015Authority(provider=provider).qualify(request)
        self.assertEqual(provider.calls, 1)
        self.assertIsInstance(caught.exception.__cause__, S015AuthorityFactsUndeclared)
        self.assertIn(S015_AUTHORITY_FACTS_SCHEMA, str(caught.exception.__cause__))
        self.assertEqual(AuthorityDecision.objects.count(), before)
