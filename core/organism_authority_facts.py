"""The declared vocabulary of S015 structured authority references (U-FACTS-1).

An authority provider reports the facts it read as a structured reference:
the prefix "s015a1:" followed by the canonical JSON of a flat object whose
keys are short names. organism_authority._facts_read turns that object into
the decision row's facts_read list, and the provider's encoder checks the
object before encoding it. Both consult the one table in this module,
S015_AUTHORITY_FACTS, so that the keys a provider may emit for an action,
the relation and reference each key names, and the shapes a refusal may
carry are declared in one place rather than implied by two readers.

The vocabulary is versioned by S015_AUTHORITY_FACTS_SCHEMA. The marker is
never written into a reference, a fact or a decision row; a row is dated by
the deployment that wrote it (see the unit's datacron entry). The reference
prefix, the refusal key and the 255-character cap are declared here as the
single source for the Python readers; migration 0037's SQL carries its own
copies, which tests/test_s015_authority_facts_schema.py pins to this table.

Two policies share the table and are kept distinct on purpose:

* the parser (organism_authority._facts_read) is compatible: it admits every
  declared key of the selected shape, requires none except in the exact
  shapes, skips the "-" sentinel, and refuses only unknown keys, invalid
  discriminators and invalid values;
* the producer (validate_authority_facts, called by the production
  provider's encoder) is strict: every required key present, every value of
  its declared kind and literal set, no undeclared key, and a refusal
  carrying exactly its code under "x".

Rows are selected in two steps. A Selector names the request-target form an
action distinguishes (none, the contextual-role event action, or the
entitlement-election target form); within a form, a qualified reference
must match exactly one FactsEntry by its reference discriminators (for
example kind and route). A refusal, marked by the "x" key, never carries
discriminators: it selects its RefusalShape by form alone.

This module imports nothing from Django or from the services; it is pure
data and pure functions so that both readers can share it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping
from uuid import UUID

from core.organism_contract import OrganismAction


S015_AUTHORITY_REFERENCE_PREFIX: Final = "s015a1:"
S015_AUTHORITY_REFERENCE_MAX_LENGTH: Final = 255
S015_AUTHORITY_FACTS_SCHEMA: Final = "S015_AUTHORITY_FACTS_V1"
S015_AUTHORITY_REFUSAL_KEY: Final = "x"
REFERENCE_SENTINEL: Final = "-"

CONSENT_U_RELATIONS: Final = MappingProxyType({
    "c:": "core_officeappointmentconsent",
    "e:": "core_contextualroleassignmentstateevent",
})
FOUNDING_RULE_PREFIX: Final = "platform-terms-version:"
FOUNDING_RULE_SUFFIX: Final = "#founding"
FOUNDING_REGISTRATION_PREFIX: Final = "learner-registration:"
FOUNDING_TERMS_PREFIX: Final = "platform-terms-acceptance:"
FOUNDING_KEYS: Final = frozenset({"rule", "registration", "terms"})
POLICY_LITERALS: Final = frozenset({"A", "B", "C"})


class S015AuthorityFactsUndeclared(ValueError):
    """A producer offered facts the table does not declare for the action.

    Raised on the producer side by validate_authority_facts and by
    ProductionApplicationAuthorityProvider._refuse when auxiliary refusal
    facts carry "x". Inside the provider boundary S015Authority.qualify
    translates it, like any provider exception, to S015AuthorityUnavailable
    with this error as the cause. The parser never raises it: a reference
    the table refuses is a S015AuthorityMalformed there.
    """
    pass


class FactKind(str, Enum):
    """How a key's value becomes a fact.

    REF: a str reference; "-" means no fact; None is refused.
    REF_OR_NULL: as REF, but None is a fact whose reference is null, meaning
        the source supplied no reference at the request's coordinates.
    ENGAGEMENT: an int engagement ordinal, rendered as
        "<target application_uuid>:<ordinal>"; a str is taken verbatim (the
        compatibility form of recorded references); "-" means no fact.
    CONSENT_U: a str "c:<consent uuid>" or "e:<proposal event uuid>", each
        naming its own relation (CONSENT_U_RELATIONS); no sentinel.
    CONSENT_W: a str consent uuid, verbatim; no sentinel.
    POLICY_K: a str nomination policy letter, recorded as a fact; on a
        PROPOSE the role definition named by the request target is recorded
        beside it (see FactsEntry.propose_role).
    FOUNDING_RULE, FOUNDING_REGISTRATION, FOUNDING_TERMS: the founding
        reference's three prefixed values, recorded by the founding block in
        a fixed order (see FactsEntry.founding_block).
    TEXT: descriptive; no fact; the producer requires a str.
    LITERAL: descriptive; no fact; the producer requires a declared literal.
    """
    REF = "REF"
    REF_OR_NULL = "REF_OR_NULL"
    ENGAGEMENT = "ENGAGEMENT"
    CONSENT_U = "CONSENT_U"
    CONSENT_W = "CONSENT_W"
    POLICY_K = "POLICY_K"
    FOUNDING_RULE = "FOUNDING_RULE"
    FOUNDING_REGISTRATION = "FOUNDING_REGISTRATION"
    FOUNDING_TERMS = "FOUNDING_TERMS"
    TEXT = "TEXT"
    LITERAL = "LITERAL"


FACT_BEARING_KINDS: Final = frozenset({
    FactKind.REF,
    FactKind.REF_OR_NULL,
    FactKind.ENGAGEMENT,
    FactKind.CONSENT_W,
    FactKind.POLICY_K,
    FactKind.FOUNDING_RULE,
    FactKind.FOUNDING_REGISTRATION,
    FactKind.FOUNDING_TERMS,
})


@dataclass(frozen=True, slots=True)
class FactKey:
    """One admissible key of a shape.

    relation names the fact's relation for the fact-bearing kinds; it is
    None for CONSENT_U (whose relation the value's prefix selects), TEXT and
    LITERAL. prefix, when given, is added by the parser to a non-null
    reference only when the value does not already start with it; the table
    declares prefixes on REF keys only. required binds the
    producer only. literals, when given, binds the producer to those
    values; the parser never checks literals.
    """
    kind: FactKind
    relation: str | None = None
    prefix: str | None = None
    required: bool = False
    literals: frozenset[str] | None = None


class Selector(str, Enum):
    """Which request-target form an action distinguishes before matching
    reference discriminators.

    NONE: one form; entries differ by reference discriminators only.
    ROLE_EVENT_ACTION: form "PROPOSE" when the target's event_action is
        "PROPOSE", otherwise "TRANSITION" (a target that is not a dict
        counts as empty).
    ELECTION_TARGET_FORM: form "SNAPSHOT" or "GATEWAY" by the exact
        predicates in election_target_form, else None.
    """
    NONE = "NONE"
    ROLE_EVENT_ACTION = "ROLE_EVENT_ACTION"
    ELECTION_TARGET_FORM = "ELECTION_TARGET_FORM"


@dataclass(frozen=True, slots=True)
class FactsEntry:
    """One qualified shape of an action.

    form: the Selector form this entry serves (None under Selector.NONE).
    discriminators: reference keys with the literal values that select this
        entry among the entries of its form; descriptive, never a fact.
    keys: the admissible keys besides the discriminators.
    exact: the qualified key set must equal discriminators plus keys.
    founding_block: emit the founding facts, in the fixed order rule
        (terms version), registration, terms (acceptance), principal, before
        the key walk; the three keys are then skipped in the walk.
    propose_role: when the "k" value is a policy letter, emit the role
        definition named by the target's role_definition_uuid before the
        key walk.
    exactly_one: groups of keys of which a qualified producer output carries
        exactly one (producer only).
    required_when: for a LITERAL key, the keys a qualified producer output
        must also carry when that literal is present (producer only).
    """
    form: str | None
    discriminators: Mapping[str, str]
    keys: Mapping[str, FactKey]
    exact: bool = False
    founding_block: bool = False
    propose_role: bool = False
    exactly_one: tuple[frozenset[str], ...] = ()
    required_when: Mapping[str, Mapping[str, frozenset[str]]] = field(
        default_factory=lambda: MappingProxyType({}),
    )


@dataclass(frozen=True, slots=True)
class RefusalShape:
    """The keys a refusal (a reference carrying "x") may carry besides "x",
    for one Selector form. exact means the refusal must be {"x"} alone.
    """
    keys: Mapping[str, FactKey]
    exact: bool = False


@dataclass(frozen=True, slots=True)
class ActionFacts:
    """The vocabulary of one recorded action: its selector, its qualified
    entries and its refusal shapes by form. A form absent from refusals
    admits an "x"-only refusal and nothing else.
    """
    selector: Selector
    entries: tuple[FactsEntry, ...]
    refusals: Mapping[str | None, RefusalShape]


@dataclass(frozen=True, slots=True)
class SelectedShape:
    """What the parser needs after selection: the admissible keys, the
    discriminator keys to skip, the exact key set when exact, and the
    derived-fact flags. For a refusal, discriminator_keys is empty and the
    flags are False.
    """
    keys: Mapping[str, FactKey]
    discriminator_keys: frozenset[str]
    exact_set: frozenset[str] | None
    founding_block: bool
    propose_role: bool


class FactsSelectionError(ValueError):
    """No entry, or more than one entry, matches a qualified reference. The
    message is the parser's S015AuthorityMalformed text.
    """
    pass


def _freeze(mapping: dict) -> Mapping:
    return MappingProxyType(dict(mapping))


def _ref(relation: str, prefix: str | None = None, *, required: bool = False) -> FactKey:
    return FactKey(FactKind.REF, relation, prefix, required)


def _ref_or_null(relation: str, *, required: bool = False) -> FactKey:
    return FactKey(FactKind.REF_OR_NULL, relation, None, required)


def _literal(*values: str, required: bool = False) -> FactKey:
    return FactKey(FactKind.LITERAL, None, None, required, frozenset(values))


def _text(*, required: bool = False) -> FactKey:
    return FactKey(FactKind.TEXT, None, None, required)


_CONFIGURATION = ("core_logovernanceconfigurationversion", "governance-configuration-version:")
_SETTINGS = ("core_loapplicationsettingsversion", "application-settings-version:")
_QUESTION = ("core_loapplicationquestionversion", "application-question-version:")
_MEMBERSHIP_TRANSITION = "core_organismmembershiptransition"
_STATE_EVENT = "core_contextualroleassignmentstateevent"
_ASSIGNMENT = "core_contextualroleassignment"
_ROLE_DEFINITION = "core_organismroledefinition"


def _coordinator_keys() -> dict[str, FactKey]:
    """The four coordinator facts every application-processing and gateway
    producer reads: configuration, membership head, office head, settings.
    """
    return {
        "c": _ref(*_CONFIGURATION, required=True),
        "m": _ref(_MEMBERSHIP_TRANSITION, required=True),
        "o": _ref(_STATE_EVENT, required=True),
        "s": _ref(*_SETTINGS, required=True),
    }


def _entry(
    form: str | None,
    discriminators: dict[str, str],
    keys: dict[str, FactKey],
    **flags,
) -> FactsEntry:
    required_when = flags.pop("required_when", {})
    return FactsEntry(
        form,
        _freeze(discriminators),
        _freeze(keys),
        required_when=_freeze({
            key: _freeze({literal: frozenset(needed) for literal, needed in by_literal.items()})
            for key, by_literal in required_when.items()
        }),
        **flags,
    )


def _refusal(keys: dict[str, FactKey], *, exact: bool = False) -> RefusalShape:
    return RefusalShape(_freeze(keys), exact)


def _undiscriminated(keys: dict[str, FactKey], refusal_keys: dict[str, FactKey] | None = None, **flags) -> ActionFacts:
    return ActionFacts(
        Selector.NONE,
        (_entry(None, {}, keys, **flags),),
        _freeze({None: _refusal(refusal_keys or {})}),
    )


_ROWS: dict[OrganismAction, ActionFacts] = {
    # 1. FOUND_LIVING_ORGANISM — producer organism_application_authority._evaluate_founding
    OrganismAction.FOUND_LIVING_ORGANISM: ActionFacts(
        Selector.NONE,
        (_entry(None, {"kind": "FOUNDING"}, {
            "rule": FactKey(FactKind.FOUNDING_RULE, "core_platformtermsversion", required=True),
            "registration": FactKey(FactKind.FOUNDING_REGISTRATION, "learner-registration", required=True),
            "terms": FactKey(FactKind.FOUNDING_TERMS, "core_platformtermsacceptance", required=True),
        }, founding_block=True),),
        _freeze({None: _refusal({})}),
    ),
    # 2. TRANSITION_LIVING_ORGANISM — _qualify_phase_advance, _qualify_birthing
    OrganismAction.TRANSITION_LIVING_ORGANISM: ActionFacts(
        Selector.NONE,
        (
            _entry(None, {"kind": "PHASE_ADVANCE"}, {
                "c": _ref(*_CONFIGURATION, required=True),
                "o": _ref(_ASSIGNMENT, required=True),
                "f": _literal("FORMATION_RULES:PLATFORM_DEFAULT@v1", required=True),
                "p": _text(required=True),
            }),
            _entry(None, {"kind": "BIRTHING"}, {
                "c": _ref(*_CONFIGURATION, required=True),
                "s": _ref(*_SETTINGS, required=True),
                "q": _ref(*_QUESTION, required=True),
                "o": _ref(_ASSIGNMENT, required=True),
                "n": _ref("core_lobirthingsnapshot", required=True),
            }),
        ),
        _freeze({None: _refusal({"p": _text()})}),
    ),
    # 3. SUBMIT_APPLICATION — _submit; "p" is a parser compatibility key (F4-C)
    OrganismAction.SUBMIT_APPLICATION: _undiscriminated(
        {
            "b": _ref("s3_il_birthing_fact_source", required=True),
            "c": _ref(*_CONFIGURATION, required=True),
            "q": _ref(*_QUESTION, required=True),
            "r": _ref("learner-registration", "learner-registration:", required=True),
            "s": _ref(*_SETTINGS, required=True),
            "p": _ref("core_application"),
        },
        {"b": _ref_or_null("s3_il_birthing_fact_source")},
    ),
    # 4. WITHDRAW_APPLICATION — _withdraw
    OrganismAction.WITHDRAW_APPLICATION: _undiscriminated({
        "a": _ref("core_application", required=True),
        "t": _literal("NONE", "LO_CSA", "LO_AUTOMATIC", required=True),
    }),
    # 5. CLAIM_APPLICATION — _qualify_application_processing
    OrganismAction.CLAIM_APPLICATION: _undiscriminated({
        **_coordinator_keys(),
        "k": _literal("C", required=True),
        "t": _literal("CQ_INACTIVE", "SOLE_COORDINATOR", required=True),
    }),
    # 6. REASSIGN_APPLICATION and 7. DECIDE_APPLICATION — _qualify_application_processing
    OrganismAction.REASSIGN_APPLICATION: _undiscriminated({
        **_coordinator_keys(),
        "a": _ref("core_csaassignment", required=True),
        "e": FactKey(FactKind.ENGAGEMENT, "core_applicationengagement", required=True),
    }),
    OrganismAction.DECIDE_APPLICATION: _undiscriminated({
        **_coordinator_keys(),
        "a": _ref("core_csaassignment", required=True),
        "e": FactKey(FactKind.ENGAGEMENT, "core_applicationengagement", required=True),
    }),
    # 8. EFFECT_APPLICATION_SETTINGS and 9. EFFECT_APPLICATION_QUESTION — _qualify_gateway_change
    OrganismAction.EFFECT_APPLICATION_SETTINGS: _undiscriminated({
        **_coordinator_keys(),
        "t": _literal("FIRST_CONFIGURATION", "CQ_INACTIVE", "CHANGE_FLAG_NO", required=True),
        "v": _ref(*_SETTINGS, required=True),
    }),
    OrganismAction.EFFECT_APPLICATION_QUESTION: _undiscriminated({
        **_coordinator_keys(),
        "t": _literal("FIRST_CONFIGURATION", "CQ_INACTIVE", "CHANGE_FLAG_NO", required=True),
        "v": _ref(*_QUESTION, required=True),
    }),
    # 10. EFFECT_GOVERNANCE_CONFIGURATION — _qualify_gateway_change ("s" is removed by the producer)
    OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION: _undiscriminated(
        {
            "c": _ref(*_CONFIGURATION, required=True),
            "m": _ref(_MEMBERSHIP_TRANSITION, required=True),
            "o": _ref(_STATE_EVENT, required=True),
            "t": _literal("FIRST_CONFIGURATION", "CQ_INACTIVE", "CHANGE_FLAG_NO", required=True),
            "v": _ref(*_CONFIGURATION, required=True),
            "i": _ref("s2b_coordinator_nomination_entitlement"),
        },
        {
            "c": _ref(*_CONFIGURATION),
            "m": _ref(_MEMBERSHIP_TRANSITION),
            "o": _ref(_STATE_EVENT),
            "i": _ref_or_null("s2b_coordinator_nomination_entitlement"),
            "v": _ref(*_CONFIGURATION),
        },
    ),
    # 11. EFFECT_ENTITLEMENT_ELECTION — _qualify_birthing (snapshot form), _qualify_gateway_change (gateway form)
    OrganismAction.EFFECT_ENTITLEMENT_ELECTION: ActionFacts(
        Selector.ELECTION_TARGET_FORM,
        (
            _entry("SNAPSHOT", {"kind": "BIRTHING"}, {
                "c": _ref(*_CONFIGURATION, required=True),
                "s": _ref(*_SETTINGS, required=True),
                "q": _ref(*_QUESTION, required=True),
                "o": _ref(_ASSIGNMENT, required=True),
                "n": _ref("core_lobirthingsnapshot", required=True),
            }, exact=True),
            _entry("GATEWAY", {}, {
                **_coordinator_keys(),
                "t": _literal("FIRST_CONFIGURATION", "CQ_INACTIVE", "CHANGE_FLAG_NO", required=True),
                "v": _ref(*_SETTINGS, required=True),
            }, exact=True),
        ),
        _freeze({
            "SNAPSHOT": _refusal({"p": _text()}),
            "GATEWAY": _refusal({}, exact=True),
            None: _refusal({"p": _text()}),
        }),
    ),
    # 12. RECORD_ENTITLEMENT_GRANT — no producer in this release
    OrganismAction.RECORD_ENTITLEMENT_GRANT: _undiscriminated({}),
    # 13. TRANSITION_CONTEXTUAL_ROLE — _qualify_role_proposal (PROPOSE), _qualify_role_transition
    OrganismAction.TRANSITION_CONTEXTUAL_ROLE: ActionFacts(
        Selector.ROLE_EVENT_ACTION,
        (
            _entry("PROPOSE", {}, {
                "m": _ref(_MEMBERSHIP_TRANSITION, required=True),
                "n": _ref(_MEMBERSHIP_TRANSITION, required=True),
                "t": _literal("SELF_PROPOSAL", "MEMBER_NOMINATION", "EFFECTIVE_COORDINATOR", required=True),
                "k": FactKey(FactKind.POLICY_K, "s2b_coordinator_nomination_policy", required=True, literals=POLICY_LITERALS),
                "i": _ref_or_null("s2b_coordinator_nomination_entitlement", required=True),
                "o": _ref(_STATE_EVENT),
                "c": _ref(*_CONFIGURATION),
                "d": FactKey(FactKind.REF, "s2b_coordinator_nomination_policy_default", literals=frozenset({"C"})),
            }, propose_role=True, exactly_one=(frozenset({"c", "d"}),)),
            _entry("TRANSITION", {}, {
                "a": _ref(_ASSIGNMENT, required=True),
                "g": _ref(_ROLE_DEFINITION, required=True),
                "k": _literal("ASSIGN", "END", "REASSIGN", required=True),
                "o": _ref(_STATE_EVENT),
                "p": _ref(_STATE_EVENT),
                "u": FactKey(FactKind.CONSENT_U),
            }, required_when={"k": {"ASSIGN": {"p", "u"}}}),
        ),
        _freeze({
            "PROPOSE": _refusal({}),
            "TRANSITION": _refusal({
                "a": _ref(_ASSIGNMENT),
                "k": _literal("ASSIGN", "END", "REASSIGN"),
                "o": _ref(_STATE_EVENT),
                "p": _ref(_STATE_EVENT),
                "u": FactKey(FactKind.CONSENT_U),
            }),
        }),
    ),
    # 14. RECORD_OFFICE_CONSENT — _qualify_office_consent
    OrganismAction.RECORD_OFFICE_CONSENT: _undiscriminated(
        {
            "a": _ref(_ASSIGNMENT, required=True),
            "p": _ref(_STATE_EVENT, required=True),
            "g": _ref(_ROLE_DEFINITION, required=True),
            "k": _literal("CONSENT", "DECLINE", "WITHDRAW", required=True),
            "u": _ref("core_organismmembership", required=True),
            "w": FactKey(FactKind.CONSENT_W, "core_officeappointmentconsent"),
        },
        {
            "a": _ref(_ASSIGNMENT),
            "p": _ref(_STATE_EVENT),
            "w": FactKey(FactKind.CONSENT_W, "core_officeappointmentconsent"),
            "o": _ref(_STATE_EVENT),
        },
    ),
    # 15. TRANSITION_MEMBERSHIP — _qualify_membership_departure
    OrganismAction.TRANSITION_MEMBERSHIP: _undiscriminated(
        {
            "k": _literal("MEMBERSHIP_DEPARTURE", required=True),
            "m": _ref(_MEMBERSHIP_TRANSITION, required=True),
        },
        {"o": _ref(_STATE_EVENT)},
    ),
    # 16. ADOPT_GOVERNING_INSTRUMENT — _qualify_governance_adoption
    OrganismAction.ADOPT_GOVERNING_INSTRUMENT: ActionFacts(
        Selector.NONE,
        (_entry(None, {"kind": "GOVERNANCE_CONSTITUTION"}, {
            "g": _ref("core_governedrule", required=True),
            "source": _literal("LO_PRESCRIPTION", "FOUNDING_BODY_PRESCRIPTION", required=True),
        }),),
        _freeze({None: _refusal({})}),
    ),
    # 17. CREATE_DORMANT_CIRCLE — _qualify_governance_circle_creation
    OrganismAction.CREATE_DORMANT_CIRCLE: ActionFacts(
        Selector.NONE,
        (_entry(None, {"kind": "GOVERNANCE"}, {
            "c": _ref("core_circle", required=True),
        }),),
        _freeze({None: _refusal({})}),
    ),
    # 18. REQUEST_GOVERNANCE_SEAT — _qualify_governance_seat_request
    OrganismAction.REQUEST_GOVERNANCE_SEAT: ActionFacts(
        Selector.NONE,
        (_entry(None, {"kind": "GOVERNANCE_SEAT_REQUEST"}, {
            "c": _ref("core_circle", required=True),
            "r": _ref("core_governedrule", required=True),
            "s": _text(required=True),
        }),),
        _freeze({None: _refusal({})}),
    ),
    # 19. ADMIT_CIRCLE_PARTICIPANT — _qualify_governance_founding_seating, _qualify_governance_constitutional_seating
    OrganismAction.ADMIT_CIRCLE_PARTICIPANT: ActionFacts(
        Selector.NONE,
        (
            _entry(None, {"kind": "GOVERNANCE", "route": "CIRCLE_FOUNDING"}, {
                "c": _ref("core_circle", required=True),
                "r": _ref("core_governedrule", required=True),
                "s": _text(required=True),
                "q": _ref("core_circlejoinrequest", required=True),
            }),
            _entry(None, {"kind": "GOVERNANCE", "route": "GOVERNANCE_CONSTITUTIONAL"}, {
                "c": _ref("core_circle", required=True),
                "r": _ref("core_governedrule", required=True),
                "s": _text(required=True),
                "q": _ref("core_circlejoinrequest", required=True),
                "constitutional_route": _literal("APPOINTMENT", required=True),
            }),
        ),
        _freeze({None: _refusal({})}),
    ),
}

S015_AUTHORITY_FACTS: Final[Mapping[OrganismAction, ActionFacts]] = MappingProxyType(_ROWS)


def election_target_form(request) -> str | None:
    """Return "SNAPSHOT", "GATEWAY" or None for an EFFECT_ENTITLEMENT_ELECTION
    request, by the exact predicates the parser applied before this table.

    SNAPSHOT: the target is a dict whose key set is exactly {action,
    organism_uuid, snapshot_uuid}, its action equals the request action's
    value, its organism_uuid equals the request's living_organism_id, its
    snapshot_uuid is exactly a str, the request's version_uuid is not None
    and its version_kind is "EFFECT".

    GATEWAY: the target is a dict whose key set is exactly {action,
    organism_uuid, version_uuid}, its action equals the request action's
    value, its organism_uuid equals the request's living_organism_id, its
    version_uuid is exactly a UUID equal to the request's version_uuid, and
    the request's version_kind is "EFFECT".

    Anything else is None.
    """
    target = getattr(request, "target", None)
    if type(target) is not dict:
        return None
    if getattr(request, "version_kind", None) != "EFFECT":
        return None
    if target.get("action") != request.action.value:
        return None
    if target.get("organism_uuid") != getattr(request, "living_organism_id", None):
        return None
    keys = set(target)
    if keys == {"action", "organism_uuid", "snapshot_uuid"}:
        if type(target.get("snapshot_uuid")) is str and getattr(request, "version_uuid", None) is not None:
            return "SNAPSHOT"
        return None
    if keys == {"action", "organism_uuid", "version_uuid"}:
        version_uuid = target.get("version_uuid")
        if type(version_uuid) is UUID and version_uuid == getattr(request, "version_uuid", None):
            return "GATEWAY"
        return None
    return None


def role_event_form(request) -> str:
    """Return "PROPOSE" when the request target's event_action is "PROPOSE",
    otherwise "TRANSITION". A target that is not a dict counts as empty.
    """
    target = getattr(request, "target", None)
    if type(target) is not dict:
        target = {}
    return "PROPOSE" if target.get("event_action") == "PROPOSE" else "TRANSITION"


def select_form(vocabulary: ActionFacts, request) -> str | None:
    """Return the Selector form of a request under its action's vocabulary."""
    if vocabulary.selector is Selector.ROLE_EVENT_ACTION:
        return role_event_form(request)
    if vocabulary.selector is Selector.ELECTION_TARGET_FORM:
        return election_target_form(request)
    return None


def select_entry(vocabulary: ActionFacts, request, content: Mapping[str, object]) -> FactsEntry:
    """Select the one qualified entry a reference matches.

    Among the entries of the request's form, an entry matches when every one
    of its discriminator keys is present in content with its declared value.
    Exactly one must match: zero or several raise FactsSelectionError with
    the parser's message "structured authority reference discriminator is
    invalid".
    """
    form = select_form(vocabulary, request)
    matches = [
        entry for entry in vocabulary.entries
        if entry.form == form and all(
            key in content and content[key] == value
            for key, value in entry.discriminators.items()
        )
    ]
    if len(matches) != 1:
        raise FactsSelectionError("structured authority reference discriminator is invalid")
    return matches[0]


def select_refusal(vocabulary: ActionFacts, request) -> RefusalShape:
    """Select the refusal shape of the request's form. A form the action
    declares no shape for admits an "x"-only refusal.
    """
    form = select_form(vocabulary, request)
    shape = vocabulary.refusals.get(form)
    if shape is None:
        return RefusalShape(MappingProxyType({}), True)
    return shape


def select_shape(vocabulary: ActionFacts, request, content: Mapping[str, object]) -> SelectedShape:
    """Select what the parser walks: the refusal shape when content carries
    "x", otherwise the one matching qualified entry. Raises
    FactsSelectionError when no qualified entry, or several, match.
    """
    if S015_AUTHORITY_REFUSAL_KEY in content:
        shape = select_refusal(vocabulary, request)
        return SelectedShape(
            shape.keys,
            frozenset(),
            frozenset({S015_AUTHORITY_REFUSAL_KEY}) if shape.exact else None,
            False,
            False,
        )
    entry = select_entry(vocabulary, request, content)
    return SelectedShape(
        entry.keys,
        frozenset(entry.discriminators),
        frozenset(entry.discriminators) | frozenset(entry.keys) if entry.exact else None,
        entry.founding_block,
        entry.propose_role,
    )


def founding_value_form(kind: FactKind, value: object) -> str | None:
    """Return the fact reference a founding value yields, or None when the
    value does not have the kind's exact form.

    FOUNDING_RULE: a str "platform-terms-version:<version>#founding" yields
    "<version>". FOUNDING_REGISTRATION: a str "learner-registration:<uuid>"
    yields itself, prefix included. FOUNDING_TERMS: a str
    "platform-terms-acceptance:<uuid>" yields "<uuid>".
    """
    if type(value) is not str:
        return None
    if kind is FactKind.FOUNDING_RULE:
        if value.startswith(FOUNDING_RULE_PREFIX) and value.endswith(FOUNDING_RULE_SUFFIX):
            return value[len(FOUNDING_RULE_PREFIX):len(value) - len(FOUNDING_RULE_SUFFIX)]
        return None
    if kind is FactKind.FOUNDING_REGISTRATION:
        return value if value.startswith(FOUNDING_REGISTRATION_PREFIX) else None
    if kind is FactKind.FOUNDING_TERMS:
        return value[len(FOUNDING_TERMS_PREFIX):] if value.startswith(FOUNDING_TERMS_PREFIX) else None
    return None


def propose_role_reference(request) -> str | None:
    """Return the str form of the request target's role_definition_uuid for
    the PROPOSE role fact, or None when the target has no usable value (a
    missing key, None, or a value that is neither a str nor a UUID).
    """
    target = getattr(request, "target", None)
    if type(target) is not dict:
        return None
    value = target.get("role_definition_uuid")
    if type(value) is str:
        return value
    if type(value) is UUID:
        return str(value)
    return None


def _value_is_lawful(key: str, spec: FactKey, value: object) -> bool:
    kind = spec.kind
    if kind in {FactKind.REF, FactKind.ENGAGEMENT, FactKind.POLICY_K, FactKind.TEXT, FactKind.LITERAL}:
        if value == REFERENCE_SENTINEL and kind in {FactKind.REF, FactKind.ENGAGEMENT} and spec.literals is None:
            return True
        if kind is FactKind.ENGAGEMENT and type(value) is int:
            return True
        if type(value) is not str:
            return False
        return spec.literals is None or value in spec.literals
    if kind is FactKind.REF_OR_NULL:
        return value is None or value == REFERENCE_SENTINEL or type(value) is str
    if kind is FactKind.CONSENT_U:
        return type(value) is str and value[:2] in CONSENT_U_RELATIONS
    if kind is FactKind.CONSENT_W:
        return type(value) is str
    return founding_value_form(kind, value) is not None


def validate_authority_facts(request, facts: Mapping[str, object], *, refusal_code: str | None) -> None:
    """The producer's strict check of a facts object before it is encoded.

    refusal_code None: facts is a qualified output. It must not carry "x";
    it must match exactly one entry of the request's form by its
    discriminators; every required key must be present; every key must be
    a discriminator or a declared key; an exact entry's key set must equal
    the declared set; every value must be lawful for its kind and literals;
    an exactly_one group must be satisfied; a required_when literal must
    bring its keys; and on a PROPOSE whose "k" is a policy letter the
    target must name a role definition.

    refusal_code given: facts is a refusal output. Its "x" must equal the
    code; its other keys must be declared by the form's refusal shape (none
    when the shape is exact) and their values lawful.

    An action with no row in the table (one qualify never records) admits
    the x-only refusal and nothing else: the production provider refuses
    an unrecognised action that way before any row is consulted.

    Every key must be a str: a facts mapping with a key of any other type
    is refused at the entrance, before any key is selected or sorted.

    Raises S015AuthorityFactsUndeclared, a ValueError, naming the schema
    marker, the action and the defect. Never raises anything else for a
    mapping input.
    """
    vocabulary = S015_AUTHORITY_FACTS.get(request.action)
    label = f"{S015_AUTHORITY_FACTS_SCHEMA}: {request.action.value}"
    if any(type(key) is not str for key in facts):
        raise S015AuthorityFactsUndeclared(f"{label} facts carry a key that is not a string")
    if vocabulary is None:
        if refusal_code is not None and set(facts) == {S015_AUTHORITY_REFUSAL_KEY} and facts[S015_AUTHORITY_REFUSAL_KEY] == refusal_code:
            return
        raise S015AuthorityFactsUndeclared(f"{label} has no declared facts vocabulary")
    if refusal_code is not None:
        if facts.get(S015_AUTHORITY_REFUSAL_KEY) != refusal_code:
            raise S015AuthorityFactsUndeclared(f"{label} refusal facts must carry x equal to the refusal code")
        shape = select_refusal(vocabulary, request)
        extra = {key: value for key, value in facts.items() if key != S015_AUTHORITY_REFUSAL_KEY}
        if shape.exact and extra:
            raise S015AuthorityFactsUndeclared(f"{label} refusal facts admit x only")
        for key, value in extra.items():
            spec = shape.keys.get(key)
            if spec is None:
                raise S015AuthorityFactsUndeclared(f"{label} refusal facts carry undeclared key {key!r}")
            if not _value_is_lawful(key, spec, value):
                raise S015AuthorityFactsUndeclared(f"{label} refusal facts carry an unlawful value for {key!r}")
        return
    if S015_AUTHORITY_REFUSAL_KEY in facts:
        raise S015AuthorityFactsUndeclared(f"{label} qualified facts must not carry x")
    try:
        entry = select_entry(vocabulary, request, facts)
    except FactsSelectionError as exc:
        raise S015AuthorityFactsUndeclared(f"{label} qualified facts match no single declared entry") from exc
    admissible = set(entry.discriminators) | set(entry.keys)
    present = set(facts)
    undeclared = present - admissible
    if undeclared:
        raise S015AuthorityFactsUndeclared(f"{label} qualified facts carry undeclared keys {sorted(undeclared)!r}")
    missing = {key for key, spec in entry.keys.items() if spec.required} - present
    if missing:
        raise S015AuthorityFactsUndeclared(f"{label} qualified facts lack required keys {sorted(missing)!r}")
    if entry.exact and present != admissible:
        raise S015AuthorityFactsUndeclared(f"{label} qualified facts must carry exactly {sorted(admissible)!r}")
    for key, value in facts.items():
        if key in entry.discriminators:
            continue
        if not _value_is_lawful(key, entry.keys[key], value):
            raise S015AuthorityFactsUndeclared(f"{label} qualified facts carry an unlawful value for {key!r}")
    for group in entry.exactly_one:
        if len(group & present) != 1:
            raise S015AuthorityFactsUndeclared(f"{label} qualified facts must carry exactly one of {sorted(group)!r}")
    for key, by_literal in entry.required_when.items():
        needed = by_literal.get(facts.get(key))
        if needed and not needed <= present:
            raise S015AuthorityFactsUndeclared(f"{label} qualified facts with {key}={facts[key]!r} require {sorted(needed)!r}")
    if entry.propose_role and facts.get("k") in POLICY_LITERALS:
        if propose_role_reference(request) is None:
            raise S015AuthorityFactsUndeclared(f"{label} qualified facts need the target's role_definition_uuid")


__all__ = [
    "ActionFacts",
    "CONSENT_U_RELATIONS",
    "FACT_BEARING_KINDS",
    "FOUNDING_KEYS",
    "FOUNDING_REGISTRATION_PREFIX",
    "FOUNDING_RULE_PREFIX",
    "FOUNDING_RULE_SUFFIX",
    "FOUNDING_TERMS_PREFIX",
    "FactKey",
    "FactKind",
    "FactsEntry",
    "FactsSelectionError",
    "POLICY_LITERALS",
    "REFERENCE_SENTINEL",
    "RefusalShape",
    "S015AuthorityFactsUndeclared",
    "S015_AUTHORITY_FACTS",
    "S015_AUTHORITY_FACTS_SCHEMA",
    "S015_AUTHORITY_REFERENCE_MAX_LENGTH",
    "S015_AUTHORITY_REFERENCE_PREFIX",
    "S015_AUTHORITY_REFUSAL_KEY",
    "SelectedShape",
    "Selector",
    "election_target_form",
    "founding_value_form",
    "propose_role_reference",
    "role_event_form",
    "select_entry",
    "select_form",
    "select_refusal",
    "select_shape",
    "validate_authority_facts",
]
