"""Canonical contracts for the bounded S015 organism substrate."""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping


ORIGINATING_INTEVIA_LO_REFERENCE: Final = "intevia-originating-lo"
ORIGINATING_MEMBERSHIP_CONTRACT_VERSION: Final = 1
S015_CONTRACT_VERSION: Final = 1
S015_AUTHORITY_ECHO_SCHEMA: Final = "S015_AUTHORITY_ECHO_V1"
RESERVED_GENESIS_AUTHORITY_NAMESPACE: Final = (
    "INTEVIA_RESERVED_GENESIS_AUTHORITY_V1"
)
GENESIS_BOOTSTRAP_REASON: Final = (
    "GENESIS_BOOTSTRAP_NAMED_BY_HUMAN_INVOCATION"
)


class OrganismAction(str, Enum):
    """The actions an S015 organism command may record.

    Each member's value is its own name. An authority request names one
    (organism_authority checks its type), and its command subtype must
    begin with that value. S015_AUTHORITY_FACTS declares the structured
    reference vocabulary per action; an action with no row there admits
    only the x-only refusal.
    """
    REGISTER_AUTHORITY_PRINCIPAL = "REGISTER_AUTHORITY_PRINCIPAL"
    RECORD_AUTHORITY_BASIS = "RECORD_AUTHORITY_BASIS"
    FOUND_LIVING_ORGANISM = "FOUND_LIVING_ORGANISM"
    CREATE_DORMANT_CIRCLE = "CREATE_DORMANT_CIRCLE"
    MARK_CIRCLE_ELIGIBLE = "MARK_CIRCLE_ELIGIBLE"
    TRANSITION_MEMBERSHIP = "TRANSITION_MEMBERSHIP"
    TRANSITION_LIVING_ORGANISM = "TRANSITION_LIVING_ORGANISM"
    SUBMIT_APPLICATION = "SUBMIT_APPLICATION"
    WITHDRAW_APPLICATION = "WITHDRAW_APPLICATION"
    CLAIM_APPLICATION = "CLAIM_APPLICATION"
    REASSIGN_APPLICATION = "REASSIGN_APPLICATION"
    DECIDE_APPLICATION = "DECIDE_APPLICATION"
    EFFECT_APPLICATION_SETTINGS = "EFFECT_APPLICATION_SETTINGS"
    EFFECT_APPLICATION_QUESTION = "EFFECT_APPLICATION_QUESTION"
    EFFECT_GOVERNANCE_CONFIGURATION = "EFFECT_GOVERNANCE_CONFIGURATION"
    EFFECT_ENTITLEMENT_ELECTION = "EFFECT_ENTITLEMENT_ELECTION"
    RECORD_ENTITLEMENT_GRANT = "RECORD_ENTITLEMENT_GRANT"
    ADOPT_GOVERNING_INSTRUMENT = "ADOPT_GOVERNING_INSTRUMENT"
    REQUEST_GOVERNANCE_SEAT = "REQUEST_GOVERNANCE_SEAT"
    ADMIT_CIRCLE_PARTICIPANT = "ADMIT_CIRCLE_PARTICIPANT"
    TRANSITION_CONTEXTUAL_ROLE = "TRANSITION_CONTEXTUAL_ROLE"
    RECORD_OFFICE_CONSENT = "RECORD_OFFICE_CONSENT"
    RECORD_ROLE_DEFINITION = "RECORD_ROLE_DEFINITION"
    TRANSITION_MEMBERSHIP_CONDITION = "TRANSITION_MEMBERSHIP_CONDITION"
    REPLACE_ESSENTIAL_COVERAGE_ROSTER = "REPLACE_ESSENTIAL_COVERAGE_ROSTER"
    TRANSITION_AUTHORITY_INVALIDATION = "TRANSITION_AUTHORITY_INVALIDATION"
    BIND_OR_TRANSITION_ACTOR_CAPACITY = "BIND_OR_TRANSITION_ACTOR_CAPACITY"
    RECORD_GOVERNED_DETERMINATION = "RECORD_GOVERNED_DETERMINATION"
    RECORD_DEPENDENCY_CLOSURE = "RECORD_DEPENDENCY_CLOSURE"
    RECORD_INDEPENDENCE_OR_UNAVAILABILITY = (
        "RECORD_INDEPENDENCE_OR_UNAVAILABILITY"
    )
    CLASSIFY_EXCEPTIONAL_TRANSFORMATION = "CLASSIFY_EXCEPTIONAL_TRANSFORMATION"
    RECORD_CONTINUITY_TRANSITION = "RECORD_CONTINUITY_TRANSITION"
    TRANSITION_OBLIGATION_CASE = "TRANSITION_OBLIGATION_CASE"
    ISSUE_VISIBILITY_GRANT = "ISSUE_VISIBILITY_GRANT"
    TRANSITION_VISIBILITY_GRANT = "TRANSITION_VISIBILITY_GRANT"
    RECORD_PLANNING_CLASSIFICATION = "RECORD_PLANNING_CLASSIFICATION"


class QualificationPlane(str, Enum):
    """The four qualification planes a command subtype is bound on:
    LEGAL ("L"), CONSTITUTIONAL ("C"), EXECUTION ("E") and PROPAGATION
    ("P").
    """
    LEGAL = "L"
    CONSTITUTIONAL = "C"
    EXECUTION = "E"
    PROPAGATION = "P"


class QualificationPlaneState(str, Enum):
    """How a command subtype stands on one qualification plane:
    PREREQUISITE_REQUIRED, OUTPUT_PRODUCED, REFERENCE_ONLY, FORBIDDEN,
    or NOT_APPLICABLE_WITH_REASON (which carries a reason).
    """
    PREREQUISITE_REQUIRED = "PREREQUISITE_REQUIRED"
    OUTPUT_PRODUCED = "OUTPUT_PRODUCED"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    FORBIDDEN = "FORBIDDEN"
    NOT_APPLICABLE_WITH_REASON = "NOT_APPLICABLE_WITH_REASON"


@dataclass(frozen=True, slots=True)
class QualificationPlaneBinding:
    """A frozen plane state with its optional reason.

    state must be exactly a QualificationPlaneState. A
    NOT_APPLICABLE_WITH_REASON state requires a non-empty exact str.
    Other states reject only such a reason; None, empty strings and
    values of other types are accepted.

    Construction raises TypeError for an invalid state, or
    ValueError for an invalid state/reason pairing.
    """
    state: QualificationPlaneState
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate the exact state type and reason pairing.

        Raise TypeError unless state has the exact
        QualificationPlaneState type.
        For NOT_APPLICABLE_WITH_REASON, require a non-empty exact str;
        whitespace is accepted. For every other state, reject only such
        a reason and accept all other values.
        Raise ValueError when the pairing fails.
        """
        if type(self.state) is not QualificationPlaneState:
            raise TypeError("state must be a QualificationPlaneState")
        requires_reason = (
            self.state is QualificationPlaneState.NOT_APPLICABLE_WITH_REASON
        )
        if requires_reason != (type(self.reason) is str and bool(self.reason)):
            raise ValueError("only not-applicable bindings require a reason")


class QualificationApplicabilityUnknown(ValueError):
    """Raised by qualification_planes for a command subtype the matrix
    does not declare; a ValueError.
    """
    pass


def _required() -> QualificationPlaneBinding:
    """Return a PREREQUISITE_REQUIRED binding."""
    return QualificationPlaneBinding(QualificationPlaneState.PREREQUISITE_REQUIRED)


def _output() -> QualificationPlaneBinding:
    """Return an OUTPUT_PRODUCED binding."""
    return QualificationPlaneBinding(QualificationPlaneState.OUTPUT_PRODUCED)


def _reference() -> QualificationPlaneBinding:
    """Return a REFERENCE_ONLY binding."""
    return QualificationPlaneBinding(QualificationPlaneState.REFERENCE_ONLY)


def _forbidden() -> QualificationPlaneBinding:
    """Return a FORBIDDEN binding."""
    return QualificationPlaneBinding(QualificationPlaneState.FORBIDDEN)


def _not_applicable(reason: str) -> QualificationPlaneBinding:
    """Return a NOT_APPLICABLE_WITH_REASON binding carrying reason."""
    return QualificationPlaneBinding(
        QualificationPlaneState.NOT_APPLICABLE_WITH_REASON,
        reason,
    )


def _planes(
    legal: QualificationPlaneBinding,
    constitutional: QualificationPlaneBinding,
    execution: QualificationPlaneBinding,
    propagation: QualificationPlaneBinding,
) -> Mapping[QualificationPlane, QualificationPlaneBinding]:
    """Return a read-only mapping of the four planes to the given
    bindings, in the order legal, constitutional, execution,
    propagation.
    """
    return MappingProxyType(
        {
            QualificationPlane.LEGAL: legal,
            QualificationPlane.CONSTITUTIONAL: constitutional,
            QualificationPlane.EXECUTION: execution,
            QualificationPlane.PROPAGATION: propagation,
        }
    )


_NO_LEGAL = _not_applicable("NO_LEGAL_CONCLUSION")
_C_REQUIRED = _required()
_E_REQUIRED = _required()
_P_FORBIDDEN = _forbidden()


def _ordinary_planes(
    legal: QualificationPlaneBinding = _NO_LEGAL,
    constitutional: QualificationPlaneBinding = _C_REQUIRED,
) -> Mapping[QualificationPlane, QualificationPlaneBinding]:
    """Return the planes of an ordinary command subtype.

    legal defaults to not applicable ("NO_LEGAL_CONCLUSION") and
    constitutional to required; execution is required and propagation
    forbidden in every case.
    """
    return _planes(legal, constitutional, _E_REQUIRED, _P_FORBIDDEN)


_plane_rows: dict[str, Mapping[QualificationPlane, QualificationPlaneBinding]] = {
    "REGISTER_AUTHORITY_PRINCIPAL/GENESIS_BOOTSTRAP": _planes(
        _NO_LEGAL,
        _not_applicable(GENESIS_BOOTSTRAP_REASON),
        _not_applicable(GENESIS_BOOTSTRAP_REASON),
        _P_FORBIDDEN,
    ),
    "REGISTER_AUTHORITY_PRINCIPAL/ORDINARY_OR_ALIAS_REVIEW": _ordinary_planes(),
    "RECORD_AUTHORITY_BASIS": _ordinary_planes(
        _not_applicable("BASIS_IS_ASSERTED_NOT_LEGALLY_QUALIFIED")
    ),
    "FOUND_LIVING_ORGANISM": _ordinary_planes(),
    "CREATE_DORMANT_CIRCLE": _ordinary_planes(),
    "MARK_CIRCLE_ELIGIBLE": _ordinary_planes(),
    "TRANSITION_MEMBERSHIP": _ordinary_planes(),
    "TRANSITION_LIVING_ORGANISM/PHASE_ADVANCE": _planes(
        _NO_LEGAL, _C_REQUIRED, _E_REQUIRED, _P_FORBIDDEN
    ),
    "TRANSITION_LIVING_ORGANISM/BIRTHING": _planes(
        _NO_LEGAL, _C_REQUIRED, _E_REQUIRED, _P_FORBIDDEN
    ),
    "ADOPT_GOVERNING_INSTRUMENT/GOVERNANCE_CONSTITUTION": _planes(
        _NO_LEGAL, _C_REQUIRED, _E_REQUIRED, _P_FORBIDDEN
    ),
    "EFFECT_ENTITLEMENT_ELECTION": _planes(
        _NO_LEGAL, _C_REQUIRED, _E_REQUIRED, _P_FORBIDDEN
    ),
    "RECORD_ENTITLEMENT_GRANT": _planes(
        _NO_LEGAL, _C_REQUIRED, _E_REQUIRED, _P_FORBIDDEN
    ),
    "ADMIT_CIRCLE_PARTICIPANT": _ordinary_planes(),
    "REQUEST_GOVERNANCE_SEAT": _ordinary_planes(),
    "SUBMIT_APPLICATION": _ordinary_planes(),
    "WITHDRAW_APPLICATION": _ordinary_planes(),
    "CLAIM_APPLICATION": _ordinary_planes(),
    "REASSIGN_APPLICATION": _ordinary_planes(),
    "DECIDE_APPLICATION": _ordinary_planes(),
    "EFFECT_APPLICATION_SETTINGS": _ordinary_planes(),
    "EFFECT_APPLICATION_QUESTION": _ordinary_planes(),
    "EFFECT_GOVERNANCE_CONFIGURATION": _ordinary_planes(),
    "TRANSITION_CONTEXTUAL_ROLE": _ordinary_planes(),
    "RECORD_OFFICE_CONSENT": _ordinary_planes(),
    "RECORD_ROLE_DEFINITION": _ordinary_planes(),
    "TRANSITION_MEMBERSHIP_CONDITION": _ordinary_planes(),
    "REPLACE_ESSENTIAL_COVERAGE_ROSTER": _ordinary_planes(),
    "TRANSITION_AUTHORITY_INVALIDATION": _ordinary_planes(),
    "BIND_OR_TRANSITION_ACTOR_CAPACITY": _ordinary_planes(),
    "RECORD_GOVERNED_DETERMINATION/LEGAL_BASIS_QUALIFICATION": _planes(
        _output(),
        _not_applicable("LEGAL_DETERMINATION_NOT_CONSTITUTIONAL_ACT"),
        _E_REQUIRED,
        _P_FORBIDDEN,
    ),
    "RECORD_GOVERNED_DETERMINATION/CONSTITUTIONAL_AUTHORITY_QUALIFICATION": _planes(
        _not_applicable("NOT_LEGAL_DETERMINATION"),
        _output(),
        _E_REQUIRED,
        _P_FORBIDDEN,
    ),
    "RECORD_GOVERNED_DETERMINATION/EXECUTION_ELIGIBILITY_QUALIFICATION": _planes(
        _not_applicable("NOT_LEGAL_DETERMINATION"),
        _not_applicable("EXECUTION_QUALIFICATION_NOT_CONSTITUTIONAL_ACT"),
        _output(),
        _P_FORBIDDEN,
    ),
    "RECORD_GOVERNED_DETERMINATION/PROPAGATION_VERIFICATION_REFERENCE": _planes(
        _not_applicable("REFERENCE_ONLY"),
        _not_applicable("REFERENCE_ONLY"),
        _E_REQUIRED,
        _reference(),
    ),
    "RECORD_GOVERNED_DETERMINATION/INDEPENDENCE_OR_UNAVAILABILITY_OR_CLOSURE_OR_CLASSIFICATION_OR_REASSESSMENT": _planes(
        _not_applicable("NOT_LEGAL_DETERMINATION"),
        _not_applicable("EVIDENCE_DETERMINATION_NOT_CONSTITUTIONAL_ACT"),
        _E_REQUIRED,
        _P_FORBIDDEN,
    ),
    "RECORD_DEPENDENCY_CLOSURE": _ordinary_planes(
        constitutional=_not_applicable("EVIDENCE_CLOSURE_NOT_CONSTITUTIONAL_ACT")
    ),
    "RECORD_INDEPENDENCE_OR_UNAVAILABILITY": _ordinary_planes(
        constitutional=_not_applicable(
            "EVIDENCE_DETERMINATION_NOT_CONSTITUTIONAL_ACT"
        )
    ),
    "CLASSIFY_EXCEPTIONAL_TRANSFORMATION": _ordinary_planes(
        _not_applicable("CLASSIFICATION_DOES_NOT_ESTABLISH_LAWFULNESS")
    ),
    "RECORD_CONTINUITY_TRANSITION": _ordinary_planes(),
    "TRANSITION_OBLIGATION_CASE/INTERNAL_ESCALATION_ONLY": _planes(
        _not_applicable("NO_LEGAL_TIMELINESS_CLAIM"),
        _not_applicable("OBLIGATION_STATE_NOT_CONSTITUTIONAL_ACT"),
        _E_REQUIRED,
        _P_FORBIDDEN,
    ),
    "TRANSITION_OBLIGATION_CASE/LEGAL_TIMELINESS_EXTENSION_REJECTION_OR_RESOLUTION": _planes(
        _required(),
        _not_applicable("OBLIGATION_STATE_NOT_CONSTITUTIONAL_ACT"),
        _E_REQUIRED,
        _P_FORBIDDEN,
    ),
    "ISSUE_VISIBILITY_GRANT": _ordinary_planes(
        _not_applicable("S015_GRANT_DOES_NOT_ESTABLISH_EXTERNAL_LEGAL_BASIS")
    ),
    "TRANSITION_VISIBILITY_GRANT": _ordinary_planes(
        _not_applicable("S015_TRANSITION_DOES_NOT_ESTABLISH_EXTERNAL_LEGAL_EFFECT")
    ),
    "RECORD_PLANNING_CLASSIFICATION": _planes(
        _NO_LEGAL,
        _not_applicable("PLANNING_LINEAGE_ONLY"),
        _E_REQUIRED,
        _P_FORBIDDEN,
    ),
}

QUALIFICATION_PLANE_MATRIX: Final = MappingProxyType(_plane_rows)


def qualification_planes(
    command_subtype: str,
) -> Mapping[QualificationPlane, QualificationPlaneBinding]:
    """Return the declared bindings from QUALIFICATION_PLANE_MATRIX.

    Look up command_subtype without a separate type check or
    enforcing the returned plane requirements. Translate lookup
    KeyError or TypeError to QualificationApplicabilityUnknown,
    preserving the cause. This includes missing rows and
    unhashable keys.

    organism_authority._validate_request uses the lookup to reject
    unknown subtypes and discards the returned bindings.
    """
    try:
        return QUALIFICATION_PLANE_MATRIX[command_subtype]
    except (KeyError, TypeError) as exc:
        raise QualificationApplicabilityUnknown(
            "S015 qualification applicability is unknown"
        ) from exc


__all__ = [
    "GENESIS_BOOTSTRAP_REASON",
    "ORIGINATING_INTEVIA_LO_REFERENCE",
    "ORIGINATING_MEMBERSHIP_CONTRACT_VERSION",
    "OrganismAction",
    "QUALIFICATION_PLANE_MATRIX",
    "QualificationApplicabilityUnknown",
    "QualificationPlane",
    "QualificationPlaneBinding",
    "QualificationPlaneState",
    "RESERVED_GENESIS_AUTHORITY_NAMESPACE",
    "S015_AUTHORITY_ECHO_SCHEMA",
    "S015_CONTRACT_VERSION",
    "qualification_planes",
]