"""The production authority provider for S015 organism commands.

ProductionApplicationAuthorityProvider can be supplied to S015Authority
to judge founding, application, membership, role, office, gateway,
entitlement, phase, Birthing and governance acts. It returns the request
type's response or refusal carrier, with a structured s015a1 authority
reference and, for a refusal, the decided and disclosed codes.

Historical reads use effective_at for state and evaluated_at for
knowledge. Some checks use current rows or a specifically defined
coordinate or same-request rule; the individual helpers state those
boundaries. Not every query is a historical read at the request's cuts.

The provider's own queries read facts without explicit command locks
or decision writes. Calling services supply transaction and locking
protection; S015Authority validates the result and records decisions
for the actions it selects. Injected fact sources are responsible for
their own behaviour.

Birthing, governed-registration and nomination-entitlement sources can
be supplied. The module also exposes _effective_version for referenced
gateway versions and fixture_terms_permitted for fixture-terms checks.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid5

from django.db import connections
from django.db.models import QuerySet

from core.models import (
    Application,
    ApplicationDecision,
    ApplicationEngagement,
    ApplicationWithdrawal,
    Circle,
    CircleStateEvent,
    CSAAssignment,
    CSAAssignmentEnd,
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    CircleJoinRequest,
    Identity,
    LivingOrganism,
    LivingOrganismEvent,
    LOFoundingContentVersion,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    LOGovernanceConfigurationVersion,
    LearnerRegistration,
    OrganismMembership,
    OrganismMembershipTransition,
    OrganismRoleDefinition,
    OfficeAppointmentConsent,
    PlatformTermsVersion,
)
from core.organism_authority_facts import (
    S015AuthorityFactsUndeclared,
    S015_AUTHORITY_FACTS_SCHEMA,
    S015_AUTHORITY_REFERENCE_MAX_LENGTH,
    S015_AUTHORITY_REFERENCE_PREFIX,
    S015_AUTHORITY_REFUSAL_KEY,
    validate_authority_facts,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    S015AuthorityRefusalCode,
    S015PreFoundingAuthorityRefusal,
    S015PreFoundingAuthorityRequest,
    S015PreFoundingAuthorityResponse,
    S015OwnApplicantAuthorityRefusal,
    S015OwnApplicantAuthorityRequest,
    S015OwnApplicantAuthorityResponse,
    S015PostFoundingAuthorityRefusal,
    S015PostFoundingAuthorityRequest,
    S015PostFoundingAuthorityResponse,
    S015AuthorityRequest,
    S015AuthorityResult,
    authority_target_fingerprint,
    canonical_json_bytes,
    identity_state_at,
)
from src.intevia.services.organism_formation_authority import founding_acceptance_uuid

# The derived admission-stage key prefix of a compound governance seating: the
# literal organism_governance_service._compound_stage_identifiers prefixes to the
# digest for ADMIT_CIRCLE_PARTICIPANT. tests/test_a1_same_request_rule.py holds
# the two equal.
_COMPOUND_ADMISSION_KEY_PREFIX = "sbg:admit:"

class BirthingFactSource(Protocol):
    """Where the provider learns whether an LO has been born.

    reference_at returns a reference to the Birthing evidence in effect for
    the LO at effective_at and known by evaluated_at, or None when there is
    none. ProductionBirthingFactSource is the production source.
    """
    def reference_at(
        self, living_organism_id: UUID, effective_at: datetime, evaluated_at: datetime,
    ) -> str | None:
        """Return the Birthing evidence reference for living_organism_id at the
        two time cuts, or None."""
        ...


class GovernedRegistrationFactSource(Protocol):
    """Where the provider learns whether an identity holds a governed
    registration.

    reference_at returns a "learner-registration:" reference for the
    identity's registration in effect at effective_at and known by
    evaluated_at, or None. ProductionGovernedRegistrationFactSource is the
    production source.
    """
    def reference_at(
        self, identity_id: UUID, effective_at: datetime, evaluated_at: datetime,
    ) -> str | None:
        """Return the registration reference for identity_id at the two time
        cuts, or None."""
        ...


class CoordinatorNominationEntitlementFactSource(Protocol):
    """Supply evidence that an LO may use entitlement-dependent Coordinator
    nomination policies.

    reference_at returns an entitlement reference at effective_at and
    evaluated_at, or None. The provider uses a non-empty string reference
    when enabling policies A or B; when evaluating a proposal without one,
    it falls back from A or B to policy C. This is not the general check
    of whether a particular person may nominate.

    ProductionCoordinatorNominationEntitlementFactSource always returns None.
    """
    def reference_at(
        self, living_organism_id: UUID, effective_at: datetime, evaluated_at: datetime,
    ) -> str | None:
        """Return the entitlement reference for living_organism_id at the two
        time cuts, or None."""
        ...


def fixture_terms_permitted(database_alias: str) -> bool:
    """Return whether this database name permits fixture platform terms.

    True only when the alias's configured NAME has type exactly str,
    starts with "test_intevia_living_organism_", and has a suffix beyond
    that prefix. Any other configured name gives False. This checks the
    name only; it does not establish that a test runner owns the database
    or that the deployment is a test environment.

    Founding qualification and the identity service use this predicate
    when deciding whether fixture terms or the test registration executor
    may be used.
    """
    database_name = connections[database_alias].settings_dict.get("NAME")
    return (
        type(database_name) is str
        and database_name.startswith("test_intevia_living_organism_")
        and database_name != "test_intevia_living_organism_"
    )


class ProductionBirthingFactSource:
    """Read the eligible recorded Birthing event at the supplied coordinates."""

    def __init__(self, *, database_alias: str = "default") -> None:
        """Bind the source to database_alias, which must be a non-empty str;
        otherwise ValueError("database_alias is required")."""
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.database_alias = database_alias

    def reference_at(
        self,
        living_organism_id: UUID,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> str | None:
        """Return "living-organism-event:<event_uuid>" for the LO's latest
        eligible BIRTHING event, or None.

        An event is eligible when its effective_at is at or before effective_at
        and its received_at and recorded_at are at or before evaluated_at. The
        latest is chosen by effective_at, then occurred_at, received_at,
        recorded_at, sequence and event_uuid, each descending. The query uses
        this source's database alias.
        """
        event_uuid = LivingOrganismEvent.objects.using(self.database_alias).filter(
            living_organism__organism_id=living_organism_id,
            action="BIRTHING",
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).order_by(
            "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
        ).values_list("event_uuid", flat=True).first()
        return None if event_uuid is None else f"living-organism-event:{event_uuid}"


class ProductionGovernedRegistrationFactSource:
    """S3-IL-REG: only a registration record proves the governed origin event.

    Reads through database_alias, "default" unless one is supplied, validated
    as ProductionBirthingFactSource validates its own (U-TIDY-1, register row
    GF-7). Construction without arguments keeps reading the default database.
    """

    def __init__(self, *, database_alias: str = "default") -> None:
        """Bind the source to database_alias, "default" unless supplied, which
        must be a non-empty str; otherwise ValueError("database_alias is
        required")."""
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.database_alias = database_alias

    def reference_at(
        self,
        identity_id: UUID,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> str | None:
        """Return "learner-registration:<registration_uuid>" for the identity's
        registration, or None.

        The registration must belong to identity_id, with effective_at at or
        before effective_at and received_at and recorded_at at or before
        evaluated_at. An identity has at most one registration (a one-to-one
        relation), so no ordering is needed. The query uses this source's
        database alias.
        """
        registration = LearnerRegistration.objects.using(self.database_alias).filter(
            identity__identity_id=identity_id,
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).first()
        if registration is None:
            return None
        return f"learner-registration:{registration.registration_uuid}"


class ProductionCoordinatorNominationEntitlementFactSource:
    """No subscription-entitlement source is installed in this release."""

    def reference_at(self, _living_organism_id: UUID, _effective_at: datetime, _evaluated_at: datetime) -> None:
        """Return None for every call: this production source supplies no
        subscription-entitlement evidence for nomination policies A or B.
        It does not decide eligibility under the fallback policy C.
        """
        return None


_VERSION_SPECS = {
    OrganismAction.EFFECT_APPLICATION_SETTINGS: (
        LOApplicationSettingsVersion,
        "application-settings-version:",
    ),
    OrganismAction.EFFECT_ENTITLEMENT_ELECTION: (
        LOApplicationSettingsVersion,
        "application-settings-version:",
    ),
    OrganismAction.EFFECT_APPLICATION_QUESTION: (
        LOApplicationQuestionVersion,
        "application-question-version:",
    ),
    OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION: (
        LOGovernanceConfigurationVersion,
        "governance-configuration-version:",
    ),
}

_OWN_ACTIONS = {OrganismAction.SUBMIT_APPLICATION, OrganismAction.WITHDRAW_APPLICATION}
_POST_ACTIONS = {
    OrganismAction.CLAIM_APPLICATION,
    OrganismAction.REASSIGN_APPLICATION,
    OrganismAction.DECIDE_APPLICATION,
    OrganismAction.EFFECT_APPLICATION_SETTINGS,
    OrganismAction.EFFECT_ENTITLEMENT_ELECTION,
    OrganismAction.EFFECT_APPLICATION_QUESTION,
    OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
    OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
    OrganismAction.RECORD_OFFICE_CONSENT,
    OrganismAction.TRANSITION_MEMBERSHIP,
    OrganismAction.TRANSITION_LIVING_ORGANISM,
    OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
    OrganismAction.CREATE_DORMANT_CIRCLE,
    OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
    OrganismAction.REQUEST_GOVERNANCE_SEAT,
}


def _fold_state(
    database_alias: str,
    token: str,
    anchor_id: int,
    effective_at: datetime,
    evaluated_at: datetime,
) -> str | None:
    """Return an anchor's state folded at two time cuts, or None.

    Calls public.s015_fold_effective_state(token, anchor_id, effective_at,
    evaluated_at) on database_alias. The database folds the anchor's events
    whose effective_at is at or before effective_at and whose received_at
    and recorded_at are at or before evaluated_at, in effective, occurred,
    received, recorded, sequence and event_uuid order, and returns the
    resulting state of the last one; None when no event is eligible. token
    names the anchor table (for example "core_organismmembership"). Takes no
    lock.
    """
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            "SELECT public.s015_fold_effective_state(%s, %s, %s, %s)",
            [token, anchor_id, effective_at, evaluated_at],
        )
        row = cursor.fetchone()
    return None if row is None else row[0]


def _effective_version(
    database_alias: str,
    organism: LivingOrganism,
    action: OrganismAction,
    effective_at: datetime,
    evaluated_at: datetime,
):
    """Return the version named by the LO's latest eligible gateway event.

    action selects application settings, the entitlement election,
    application question or governance configuration; another action
    raises KeyError. Eligible events belong to this LO and action, have
    effective_at at or before effective_at, and have received_at and
    recorded_at at or before evaluated_at. The latest is selected by
    effective_at, occurred_at, received_at, recorded_at, sequence and
    event_uuid, all descending.

    The selected event must carry the action's evidence prefix followed
    by a UUID Python can parse. Return None for a missing event or reference,
    a different prefix, an invalid UUID suffix, or no matching version.
    There is no fallback to an earlier event.

    The version must match the UUID and LO. Application settings require
    both entitlement choice and route to be null; an entitlement election
    requires both to be non-null. The version row's own timestamps are
    not filtered here, and its completeness is not checked.

    Reads on database_alias without explicit row locks. Also used by the
    formation, entitlement and governance services.
    """
    model, prefix = _VERSION_SPECS[action]
    reference = LivingOrganismEvent.objects.using(database_alias).filter(
        living_organism=organism,
        action=action.value,
        effective_at__lte=effective_at,
        received_at__lte=evaluated_at,
        recorded_at__lte=evaluated_at,
    ).order_by(
        "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
    ).values_list("evidence_reference", flat=True).first()
    if reference is None or not reference.startswith(prefix):
        return None
    try:
        version_uuid = UUID(reference[len(prefix):])
    except ValueError:
        return None
    versions = model.objects.using(database_alias).filter(
        version_uuid=version_uuid,
        living_organism=organism,
    )
    if action is OrganismAction.EFFECT_APPLICATION_SETTINGS:
        versions = versions.filter(
            entitlement_choice__isnull=True,
            entitlement_election_route__isnull=True,
        )
    elif action is OrganismAction.EFFECT_ENTITLEMENT_ELECTION:
        versions = versions.filter(
            entitlement_choice__isnull=False,
            entitlement_election_route__isnull=False,
        )
    return versions.first()


def _latest_transition(
    database_alias: str,
    model,
    anchor_field: str,
    anchor_id: int,
    effective_at: datetime,
    evaluated_at: datetime,
):
    """Return the anchor's latest transition row at the supplied cuts, or None.

    model is the transition model and anchor_field its anchor foreign key.
    A row is eligible when its effective_at is at or before effective_at
    and its recorded_at is at or before evaluated_at. There is no
    received_at eligibility predicate, unlike _fold_state.

    Select by effective_at, occurred_at, received_at, recorded_at, sequence
    and event_uuid, all descending. Read on database_alias without
    explicit row locks.
    """
    return model.objects.using(database_alias).filter(
        **{anchor_field: anchor_id},
        effective_at__lte=effective_at,
        recorded_at__lte=evaluated_at,
    ).order_by(
        "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
    ).first()


def _assignment_term_in_effect(
    database_alias: str,
    assignment_id: int,
    effective_at: datetime,
    evaluated_at: datetime,
) -> bool:
    """Return whether a role assignment's term is in effect at effective_at.

    Read its latest ASSIGN event whose effective_at is at or before
    effective_at and whose received_at and recorded_at are at or before
    evaluated_at. Select by effective_at, occurred_at, received_at,
    recorded_at, sequence and event_uuid, all descending.

    False when there is no eligible ASSIGN event, or its term is FIXED
    with a non-null effective_until at or before effective_at; True
    otherwise. This checks the term only, not the assignment's state.
    Read on database_alias without explicit row locks.
    """
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT effective_until_state, effective_until
              FROM public.core_contextualroleassignmentstateevent
             WHERE assignment_id = %s AND action = 'ASSIGN'
               AND effective_at <= %s AND received_at <= %s AND recorded_at <= %s
             ORDER BY effective_at DESC, occurred_at DESC, received_at DESC,
                      recorded_at DESC, sequence DESC, event_uuid DESC
             LIMIT 1
            """,
            [assignment_id, effective_at, evaluated_at, evaluated_at],
        )
        term = cursor.fetchone()
    if term is None:
        return False
    return not (
        term[0] == "FIXED"
        and term[1] is not None
        and effective_at >= term[1]
    )


class ProductionApplicationAuthorityProvider:
    """Judge S015 organism authority using recorded facts and supplied sources.

    S015Authority.qualify calls evaluate_organism_authority inside its
    caller's atomic block and checks the returned response or refusal
    carrier. This provider identifies itself as
    "INTEVIA.S015.ApplicationAuthorityProvider", version "R8.0".

    Its own queries use database_alias. Historical state and knowledge
    usually use effective_at and evaluated_at, but current identity and
    head checks, the engagement's settings basis, and the specific
    same-request and entitlement-candidate rules have different read
    contracts. These are described by the individual helpers.

    The provider issues reads and does not explicitly acquire command
    locks or write decisions. Transaction and locking protection belongs
    to the calling services; S015Authority records selected decisions.
    Supplied fact sources are not checked here for alias or side effects.

    Qualified responses and refusals carry structured s015a1 facts.
    Refusals distinguish the decided code from the code disclosed to the
    actor. Errors from reads, sources or fact validation can propagate to
    S015Authority.qualify, which translates provider exceptions.
    """
    provider_identity = "INTEVIA.S015.ApplicationAuthorityProvider"
    provider_version = "R8.0"

    def __init__(
        self,
        *,
        database_alias: str = "default",
        birthing_source: BirthingFactSource | None = None,
        registration_source: GovernedRegistrationFactSource | None = None,
        nomination_entitlement_source: CoordinatorNominationEntitlementFactSource | None = None,
    ) -> None:
        """Bind the provider's database alias and three fact sources.

        database_alias defaults to "default" and must have type exactly str
        and be non-empty; otherwise raise ValueError("database_alias is required").

        Each source is selected by truth value: None or another false-valued
        source is replaced by its production implementation. Default Birthing
        and registration sources use the provider's alias; the default
        nomination-entitlement source always returns None. Supplied truthy
        sources are retained without interface or database-alias validation.
        """
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.database_alias = database_alias
        self.birthing_source = birthing_source or ProductionBirthingFactSource(database_alias=database_alias)
        self.registration_source = registration_source or ProductionGovernedRegistrationFactSource(
            database_alias=database_alias,
        )
        self.nomination_entitlement_source = (
            nomination_entitlement_source or ProductionCoordinatorNominationEntitlementFactSource()
        )

    def evaluate_organism_authority(self, request: S015AuthorityRequest) -> S015AuthorityResult:
        """Judge an authority request and return its response or refusal carrier.

        S015Authority.qualify validates the request before calling this method.
        An exact S015PreFoundingAuthorityRequest goes directly to
        _evaluate_founding. For the other supported request types, check in order:

        1. An own-applicant request uses SUBMIT_APPLICATION or
           WITHDRAW_APPLICATION; a post-founding request uses a supported
           post-founding action. Otherwise return UNRECOGNISED_ACTION.
        2. command_subtype equals the action value, or is its permitted
           PHASE_ADVANCE, BIRTHING or GOVERNANCE_CONSTITUTION subtype;
           otherwise return UNRECOGNISED_ACTION. Later action-specific checks
           further constrain these combinations.
        3. For TRANSITION_MEMBERSHIP, a missing target or any resulting_state
           other than ENDED returns C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED.
        4. Require effective_at, target and LO identity, matching target action,
           LO identity and target fingerprint; otherwise UNRECOGNISED_ACTION.
           Role, office-consent and membership targets use living_organism_id
           as a string; the other targets use organism_uuid as a UUID.
        5. An own-applicant target must name the actor as applicant; otherwise
           E_NOT_APPLICANT, disclosed as DENIED.
        6. Apply the post-founding action's target checks. Exact key sets apply
           to the specified phase, Birthing, governance and entitlement forms,
           not universally. Other branches check their required event action,
           UUIDs, version identity/kind or application UUID. Failure returns
           UNRECOGNISED_ACTION.
        7. Resolve the LO. Absence returns C_GATEWAY_NOT_OPEN for submission
           and DENIED otherwise. An own-applicant request naming a different
           LO primary key returns E_NOT_APPLICANT, disclosed as DENIED.
        8. Except for submission, require the actor's current identity and
           credential checks through _identity_active; failure returns
           E_IDENTITY_INACTIVE. Submission checks historical identity in _submit.

        Dispatch to submission, withdrawal, membership departure, role
        proposal/transition, office consent or _coordinator_act. Refusals are
        returned, not raised as S015AuthorityDenied here. Reads and delegated
        checks can raise exceptions; this method does not translate them.
        It writes no decision and acquires no explicit command locks.
        """
        if type(request) is S015PreFoundingAuthorityRequest:
            return self._evaluate_founding(request)
        if type(request) is S015OwnApplicantAuthorityRequest:
            if request.action not in _OWN_ACTIONS:
                return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        elif type(request) is S015PostFoundingAuthorityRequest:
            if request.action not in _POST_ACTIONS:
                return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        else:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)

        phase_advance_subtype = f"{OrganismAction.TRANSITION_LIVING_ORGANISM.value}/PHASE_ADVANCE"
        birthing_subtype = f"{OrganismAction.TRANSITION_LIVING_ORGANISM.value}/BIRTHING"
        governance_adoption_subtype = (
            f"{OrganismAction.ADOPT_GOVERNING_INSTRUMENT.value}/GOVERNANCE_CONSTITUTION"
        )
        governance_circle_subtype = OrganismAction.CREATE_DORMANT_CIRCLE.value
        governance_seating_subtype = OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value
        if request.command_subtype != request.action.value and not (
            request.action is OrganismAction.TRANSITION_LIVING_ORGANISM
            and request.command_subtype in {phase_advance_subtype, birthing_subtype}
        ) and not (
            request.action is OrganismAction.ADOPT_GOVERNING_INSTRUMENT
            and request.command_subtype == governance_adoption_subtype
        ) and not (
            request.action is OrganismAction.CREATE_DORMANT_CIRCLE
            and request.command_subtype == governance_circle_subtype
        ) and not (
            request.action is OrganismAction.ADMIT_CIRCLE_PARTICIPANT
            and request.command_subtype == governance_seating_subtype
        ):
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        if request.action is OrganismAction.TRANSITION_MEMBERSHIP and (
            request.target is None or request.target.get("resulting_state") != "ENDED"
        ):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED,
                S015AuthorityRefusalCode.C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED,
            )
        target_organism_matches = False
        if request.target is not None and request.living_organism_id is not None:
            if request.action in {
                OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
                OrganismAction.RECORD_OFFICE_CONSENT,
                OrganismAction.TRANSITION_MEMBERSHIP,
            }:
                target_organism_matches = (
                    request.target.get("living_organism_id") == str(request.living_organism_id)
                )
            else:
                target_organism_matches = (
                    request.target.get("organism_uuid") == request.living_organism_id
                )
        if (
            request.effective_at is None
            or request.target is None
            or request.living_organism_id is None
            or request.target.get("action") != request.action.value
            or not target_organism_matches
            or authority_target_fingerprint(request.target) != request.target_fingerprint
        ):
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        if type(request) is S015OwnApplicantAuthorityRequest and (
            request.target.get("applicant_identity_uuid") != request.actor_identity_id
        ):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_NOT_APPLICANT,
                S015AuthorityRefusalCode.DENIED,
            )
        if type(request) is S015PostFoundingAuthorityRequest:
            if request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE:
                if (
                    request.target.get("living_organism_id") != str(request.living_organism_id)
                    or request.target.get("event_action") not in {"PROPOSE", "ASSIGN", "END", "REASSIGN"}
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            elif request.action is OrganismAction.RECORD_OFFICE_CONSENT:
                if request.target.get("event_action") not in {"CONSENT", "DECLINE", "WITHDRAW"}:
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                for key in ("assignment_uuid", "membership_uuid", "proposal_event_uuid"):
                    try:
                        if str(UUID(request.target[key])) != request.target[key]:
                            raise ValueError
                    except (KeyError, TypeError, ValueError):
                        return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            elif request.action is OrganismAction.TRANSITION_MEMBERSHIP:
                try:
                    if str(UUID(request.target["membership_uuid"])) != request.target["membership_uuid"]:
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            elif request.action is OrganismAction.TRANSITION_LIVING_ORGANISM:
                if request.command_subtype == phase_advance_subtype:
                    valid_target = (
                        set(request.target) == {"action", "organism_uuid", "from_phase", "to_phase"}
                        and type(request.target.get("from_phase")) is str
                        and type(request.target.get("to_phase")) is str
                    )
                else:
                    try:
                        snapshot_uuid = UUID(request.target["snapshot_uuid"])
                        valid_snapshot_uuid = str(snapshot_uuid) == request.target["snapshot_uuid"]
                    except (KeyError, TypeError, ValueError):
                        valid_snapshot_uuid = False
                    valid_target = (
                        request.command_subtype == birthing_subtype
                        and set(request.target) == {"action", "organism_uuid", "snapshot_uuid"}
                        and valid_snapshot_uuid
                    )
                if not valid_target:
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            elif request.action is OrganismAction.ADOPT_GOVERNING_INSTRUMENT:
                if (
                    request.command_subtype != governance_adoption_subtype
                    or set(request.target) != {
                        "action", "organism_uuid", "rule_uuid", "version", "source", "body",
                        "effective_from",
                    }
                    or type(request.target.get("rule_uuid")) is not str
                    or type(request.target.get("version")) is not int
                    or request.target.get("version", 0) < 1
                    or type(request.target.get("source")) is not str
                    or type(request.target.get("body")) is not dict
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                try:
                    rule_uuid = UUID(request.target["rule_uuid"])
                except (TypeError, ValueError):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                if str(rule_uuid) != request.target["rule_uuid"]:
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            elif request.action is OrganismAction.CREATE_DORMANT_CIRCLE:
                if (
                    request.command_subtype != governance_circle_subtype
                    or set(request.target) != {"action", "organism_uuid", "circle_uuid", "kind"}
                    or request.target.get("kind") != "GOVERNANCE"
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                try:
                    circle_uuid = UUID(request.target["circle_uuid"])
                except (KeyError, TypeError, ValueError):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                if str(circle_uuid) != request.target["circle_uuid"]:
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            elif request.action is OrganismAction.REQUEST_GOVERNANCE_SEAT:
                if (
                    request.command_subtype != request.action.value
                    or set(request.target) != {
                        "action", "organism_uuid", "circle_uuid", "kind",
                        "seat_rule_uuid", "seat_id", "applicant_identity_uuid",
                    }
                    or request.target.get("kind") != "GOVERNANCE"
                    or type(request.target.get("seat_id")) is not str
                    or request.target.get("applicant_identity_uuid")
                    != str(request.actor_identity_id)
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                try:
                    circle_uuid = UUID(request.target["circle_uuid"])
                    rule_uuid = UUID(request.target["seat_rule_uuid"])
                    applicant_uuid = UUID(request.target["applicant_identity_uuid"])
                except (KeyError, TypeError, ValueError):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                if (
                    str(circle_uuid) != request.target["circle_uuid"]
                    or str(rule_uuid) != request.target["seat_rule_uuid"]
                    or str(applicant_uuid) != request.target["applicant_identity_uuid"]
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            elif request.action is OrganismAction.ADMIT_CIRCLE_PARTICIPANT:
                founding_target_keys = {
                    "action", "organism_uuid", "circle_uuid", "kind",
                    "seat_rule_uuid", "seat_id", "route",
                    "request_uuid", "applicant_identity_uuid",
                }
                constitutional_target_keys = founding_target_keys | {
                    "constitutional_route", "deciding_party", "outcome",
                }
                target_route = request.target.get("route")
                if (
                    request.command_subtype != governance_seating_subtype
                    or request.target.get("kind") != "GOVERNANCE"
                    or type(request.target.get("seat_id")) is not str
                    or (
                        target_route == "CIRCLE_FOUNDING"
                        and set(request.target) != founding_target_keys
                    )
                    or (
                        target_route == "GOVERNANCE_CONSTITUTIONAL"
                        and (
                            set(request.target) != constitutional_target_keys
                            or request.target.get("constitutional_route") != "APPOINTMENT"
                            or request.target.get("deciding_party") != "office:LO_COORDINATOR"
                            or request.target.get("outcome") not in {"ACCEPT", "DECLINE"}
                        )
                    )
                    or target_route not in {"CIRCLE_FOUNDING", "GOVERNANCE_CONSTITUTIONAL"}
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                try:
                    circle_uuid = UUID(request.target["circle_uuid"])
                    rule_uuid = UUID(request.target["seat_rule_uuid"])
                    request_uuid = UUID(request.target["request_uuid"])
                    applicant_identity_uuid = UUID(request.target["applicant_identity_uuid"])
                    if (
                        str(request_uuid) != request.target["request_uuid"]
                        or str(applicant_identity_uuid)
                        != request.target["applicant_identity_uuid"]
                    ):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                if (
                    str(circle_uuid) != request.target["circle_uuid"]
                    or str(rule_uuid) != request.target["seat_rule_uuid"]
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            elif request.action in _VERSION_SPECS:
                if (
                    request.action is OrganismAction.EFFECT_ENTITLEMENT_ELECTION
                    and request.command_subtype == request.action.value
                ):
                    target = request.target
                    if type(target) is not dict:
                        return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
                    if set(target) == {"action", "organism_uuid", "snapshot_uuid"}:
                        try:
                            snapshot_uuid = UUID(target["snapshot_uuid"])
                        except (KeyError, TypeError, ValueError):
                            return self._refuse(
                                request,
                                S015AuthorityRefusalCode.UNRECOGNISED_ACTION,
                            )
                        if (
                            target.get("action") != request.action.value
                            or target.get("organism_uuid") != request.living_organism_id
                            or str(snapshot_uuid) != target["snapshot_uuid"]
                            or request.version_uuid is None
                            or request.version_kind != "EFFECT"
                        ):
                            return self._refuse(
                                request,
                                S015AuthorityRefusalCode.UNRECOGNISED_ACTION,
                            )
                    elif set(target) == {"action", "organism_uuid", "version_uuid"}:
                        if (
                            target.get("action") != request.action.value
                            or target.get("organism_uuid") != request.living_organism_id
                            or type(target.get("version_uuid")) is not UUID
                            or target.get("version_uuid") != request.version_uuid
                            or request.version_kind != "EFFECT"
                        ):
                            return self._refuse(
                                request,
                                S015AuthorityRefusalCode.UNRECOGNISED_ACTION,
                            )
                    else:
                        return self._refuse(
                            request,
                            S015AuthorityRefusalCode.UNRECOGNISED_ACTION,
                        )
                elif (
                    request.target.get("version_uuid") != request.version_uuid
                    or request.version_kind not in {"DRAFT", "EFFECT"}
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            elif type(request.target.get("application_uuid")) is not UUID:
                return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)

        organism = LivingOrganism.objects.using(self.database_alias).filter(
            organism_id=request.living_organism_id,
        ).first()
        if organism is None:
            code = (
                S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN
                if request.action is OrganismAction.SUBMIT_APPLICATION
                else S015AuthorityRefusalCode.DENIED
            )
            return self._refuse(request, code)
        if (
            type(request) is S015OwnApplicantAuthorityRequest
            and organism.pk != request.living_organism_pk
        ):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_NOT_APPLICANT,
                S015AuthorityRefusalCode.DENIED,
            )

        if (
            request.action is not OrganismAction.SUBMIT_APPLICATION
            and not self._identity_active(request)
        ):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
            )

        if request.action is OrganismAction.SUBMIT_APPLICATION:
            return self._submit(request, organism)
        if request.action is OrganismAction.WITHDRAW_APPLICATION:
            return self._withdraw(request, organism)
        if request.action is OrganismAction.TRANSITION_MEMBERSHIP:
            return self._qualify_membership_departure(request, organism)
        if request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE:
            return self._qualify_role_proposal(request, organism)
        if request.action is OrganismAction.RECORD_OFFICE_CONSENT:
            return self._qualify_office_consent(request, organism)
        return self._coordinator_act(request, organism)

    def _evaluate_founding(
        self,
        request: S015PreFoundingAuthorityRequest,
    ) -> S015AuthorityResult:
        """Qualify a FOUND_LIVING_ORGANISM request, or refuse it.

        The target must carry exactly the founding keys with the request's
        own founder and organism identities and no instruments; the rule
        must name a platform terms version in force in its exact
        "platform-terms-version:<uuid>#founding" form; the registration
        source must prove the founder's registration in its exact
        "learner-registration:<uuid>" form; and the identity must be active
        at the request's coordinates. A qualified result carries the
        FOUNDING reference (kind, rule, registration, terms) encoded through
        _encode_facts, or the size refusal if it would exceed the cap.
        """
        target = request.target
        expected_keys = {
            "action",
            "founder_identity_uuid",
            "proposed_organism_uuid",
            "founding_rule_reference",
            "instrument_references",
        }
        if (
            request.action is not OrganismAction.FOUND_LIVING_ORGANISM
            or request.command_subtype != OrganismAction.FOUND_LIVING_ORGANISM.value
            or target is None
            or set(target) != expected_keys
            or target.get("action") != OrganismAction.FOUND_LIVING_ORGANISM.value
            or target.get("founder_identity_uuid") != request.actor_identity_id
            or target.get("proposed_organism_uuid") != request.living_organism_id
            or target.get("instrument_references") != []
        ):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.UNRECOGNISED_ACTION,
                S015AuthorityRefusalCode.UNRECOGNISED_ACTION,
            )
        rule_reference = target.get("founding_rule_reference")
        if type(rule_reference) is not str or not rule_reference.startswith("platform-terms-version:"):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
                S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
            )
        version_text = rule_reference.removeprefix("platform-terms-version:").removesuffix("#founding")
        try:
            version_uuid = UUID(version_text)
        except (TypeError, ValueError):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
                S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
            )
        if rule_reference != f"platform-terms-version:{version_uuid}#founding":
            return self._refuse(
                request,
                S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
                S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
            )

        eligible_versions = PlatformTermsVersion.objects.using(self.database_alias).filter(
            effective_from__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        )
        superseded_ids = eligible_versions.filter(
            supersedes__isnull=False,
        ).values_list("supersedes_id", flat=True)
        current_version = (
            eligible_versions.exclude(pk__in=superseded_ids)
            .order_by("-effective_from", "-recorded_at", "-pk")
            .first()
        )
        requested_version = eligible_versions.filter(terms_version_uuid=version_uuid).first()
        if requested_version is None or current_version is None or requested_version.pk != current_version.pk:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.FOUNDING_TERMS_VERSION_SUPERSEDED,
                S015AuthorityRefusalCode.FOUNDING_TERMS_VERSION_SUPERSEDED,
            )
        if requested_version.is_fixture and not fixture_terms_permitted(
            self.database_alias
        ):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
                S015AuthorityRefusalCode.FOUNDING_TERMS_NOT_ACCEPTED,
            )

        registration_reference = self.registration_source.reference_at(
            request.actor_identity_id,
            request.effective_at,
            request.evaluated_at,
        )
        registration_uuid = self._registered_reference_uuid(
            request.actor_identity_id,
            registration_reference,
            request.effective_at,
            request.evaluated_at,
        )
        if registration_uuid is None:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_NOT_REGISTERED,
                S015AuthorityRefusalCode.E_NOT_REGISTERED,
            )
        if not self._identity_active_at(request):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
            )

        acceptance_uuid = founding_acceptance_uuid(request.living_organism_id)
        authority_reference = self._encode_facts(request, {
            "kind": "FOUNDING",
            "rule": rule_reference,
            "registration": registration_reference,
            "terms": f"platform-terms-acceptance:{acceptance_uuid}",
        }, refusal_code=None)
        if len(authority_reference) > S015_AUTHORITY_REFERENCE_MAX_LENGTH:
            return self._refusal_for_size(request)
        return S015PreFoundingAuthorityResponse(
            **asdict(request),
            authority_reference=authority_reference,
        )

    def _qualify_membership_departure(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify a member leaving the LO (TRANSITION_MEMBERSHIP to ENDED).

        evaluate_organism_authority has checked the canonical
        target["membership_uuid"] and the ENDED resulting state. In order:
        - membership_uuid parses, else UNRECOGNISED_ACTION;
        - the membership is in this LO and is the actor's own, else DENIED;
        - it folds ACTIVE or PROBATIONARY at the time cuts, else
          C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED;
        - the request's locked heads match it (_locked_heads_match), else
          DENIED;
        - it holds no office: an assignment of any role whose row state is
          ACTIVE, with a head event, refuses
          C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED, disclosed as itself,
          with that head event as fact "o";
        - the LO has capacity (_lo_capacity), else C_LO_NO_CAPACITY, and folds
          ACTIVE: DORMANT is C_LO_PAUSED, CLOSED is C_LO_CLOSED and any other
          state C_LO_NO_CAPACITY.

        The office check reads the assignment rows' current state, not a fold
        at effective_at. Other codes are disclosed by _refuse's rule.
        Qualified facts: "k" MEMBERSHIP_DEPARTURE and "m" the membership's
        effective head event (_latest_transition). Reads only.
        """
        try:
            membership_uuid = UUID(request.target["membership_uuid"])
        except (KeyError, TypeError, ValueError):
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        membership = OrganismMembership.objects.using(self.database_alias).filter(
            membership_uuid=membership_uuid,
            living_organism=organism,
        ).select_related("identity").first()
        if membership is None or membership.identity.identity_id != request.actor_identity_id:
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        current_state = _fold_state(
            self.database_alias,
            "core_organismmembership",
            membership.pk,
            request.effective_at,
            request.evaluated_at,
        )
        if current_state not in {"ACTIVE", "PROBATIONARY"}:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED,
            )
        if not self._locked_heads_match(request, membership):
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        active_office = ContextualRoleAssignment.objects.using(self.database_alias).filter(
            membership=membership,
            state="ACTIVE",
            head_state_event__isnull=False,
        ).select_related("head_state_event").order_by("pk").first()
        if active_office is not None:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED,
                S015AuthorityRefusalCode.C_MEMBERSHIP_LEAVE_WITH_OFFICE_NOT_IMPLEMENTED,
                facts={"o": str(active_office.head_state_event.event_uuid)},
            )
        if not self._lo_capacity(organism, request):
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        lo_state = self._lo_state(organism, request)
        if lo_state == "DORMANT":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_PAUSED)
        if lo_state == "CLOSED":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_CLOSED)
        if lo_state != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        head = _latest_transition(
            self.database_alias,
            OrganismMembershipTransition,
            "membership_id",
            membership.pk,
            request.effective_at,
            request.evaluated_at,
        )
        return self._qualified(request, {
            "k": "MEMBERSHIP_DEPARTURE",
            "m": "-" if head is None else str(head.event_uuid),
        })

    def _qualify_role_proposal(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify a Coordinator proposal, or delegate a role transition.

        For TRANSITION_CONTEXTUAL_ROLE, ASSIGN, END and REASSIGN delegate to
        _qualify_role_transition; an event_action other than PROPOSE is
        UNRECOGNISED_ACTION. For PROPOSE, check in order:
        - LO capacity, else C_LO_NO_CAPACITY; then its folded state: DORMANT
          is C_LO_PAUSED, CLOSED is C_LO_CLOSED, and any other non-ACTIVE
          state is C_LO_NO_CAPACITY;
        - at least one eligible Coordinator, else
          C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED;
        - parseable membership_uuid and role_definition_uuid, else
          UNRECOGNISED_ACTION;
        - a nominee membership in this LO folding ACTIVE, and this LO's
          active LIVING_ORGANISM-scope COORDINATOR definition, else DENIED;
        - the actor's membership in this LO, matching supplied locked heads
          and folding ACTIVE, else DENIED.

        Folds, configuration selection and effective heads use the request's
        effective_at and evaluated_at. Select the configuration's nomination
        policy, or C if the configuration is absent or its policy is false-
        valued. A selected value outside A, B and C is DENIED. Read the
        nomination-entitlement source for every valid selected policy; A or B
        falls back to C unless its reference has type exactly str and is
        non-empty. The production source returns None in this release.

        Policy B, and policy C when proposer and nominee differ, require an
        effective Coordinator office, else E_NOT_EFFECTIVE_COORDINATOR; their
        route is EFFECTIVE_COORDINATOR. Otherwise use SELF_PROPOSAL for the
        same membership and MEMBER_NOMINATION for different memberships.

        Facts supplied to _qualified are: "m" and "n", the proposer's and
        nominee's effective membership heads; "t", the route; "k", the policy
        actually applied; "i", the entitlement reference as returned; and
        "o", the effective office head when an office is required. The head
        encoding has a "-" fallback, although the earlier locked-head check
        requires the proposer's effective head. Add "c", the configuration
        UUID, when a configuration exists and its policy is not None, even
        when A or B fell back to C; otherwise add "d": "C".

        Refusals use _refuse's disclosure rule. The shared fact builders'
        validation and size rules apply. This method's own queries read
        without acquiring command locks or writing decisions.
        """
        event_action = request.target.get("event_action")
        if event_action in {"ASSIGN", "END", "REASSIGN"}:
            return self._qualify_role_transition(request, organism)
        if event_action != "PROPOSE":
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        if not self._lo_capacity(organism, request):
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        lo_state = self._lo_state(organism, request)
        if lo_state == "DORMANT":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_PAUSED)
        if lo_state == "CLOSED":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_CLOSED)
        if lo_state != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        if self._eligible_coordinator_count(organism, request) == 0:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED,
            )

        try:
            membership_uuid = UUID(request.target["membership_uuid"])
            role_uuid = UUID(request.target["role_definition_uuid"])
        except (KeyError, TypeError, ValueError):
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        nominee = OrganismMembership.objects.using(self.database_alias).filter(
            membership_uuid=membership_uuid,
            living_organism=organism,
        ).first()
        role = OrganismRoleDefinition.objects.using(self.database_alias).filter(
            role_uuid=role_uuid,
            living_organism=organism,
            active=True,
            scope="LIVING_ORGANISM",
            code="COORDINATOR",
        ).first()
        if nominee is None or role is None or _fold_state(
            self.database_alias, "core_organismmembership", nominee.pk,
            request.effective_at, request.evaluated_at,
        ) != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)

        proposer = OrganismMembership.objects.using(self.database_alias).filter(
            identity_id=request.actor_pk,
            living_organism=organism,
        ).first()
        if proposer is None or not self._locked_heads_match(request, proposer) or _fold_state(
            self.database_alias, "core_organismmembership", proposer.pk,
            request.effective_at, request.evaluated_at,
        ) != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)

        configuration = _effective_version(
            self.database_alias,
            organism,
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            request.effective_at,
            request.evaluated_at,
        )
        selected_policy = "C" if configuration is None else (
            configuration.coordinator_nomination_policy or "C"
        )
        if selected_policy not in {"A", "B", "C"}:
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        entitlement_reference = self.nomination_entitlement_source.reference_at(
            organism.organism_id, request.effective_at, request.evaluated_at,
        )
        policy = selected_policy
        if selected_policy in {"A", "B"} and (type(entitlement_reference) is not str or not entitlement_reference):
            policy = "C"

        proposer_is_nominee = proposer.pk == nominee.pk
        proposal_route = "SELF_PROPOSAL" if proposer_is_nominee else "MEMBER_NOMINATION"
        office = None
        requires_coordinator = policy == "B" or (policy == "C" and not proposer_is_nominee)
        if requires_coordinator:
            office = self._effective_coordinator_office(request, proposer)
            if office is None:
                return self._refuse(request, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
            proposal_route = "EFFECTIVE_COORDINATOR"
        elif policy == "A" and not proposer_is_nominee:
            proposal_route = "MEMBER_NOMINATION"

        proposer_head = _latest_transition(
            self.database_alias, OrganismMembershipTransition, "membership_id", proposer.pk,
            request.effective_at, request.evaluated_at,
        )
        nominee_head = _latest_transition(
            self.database_alias, OrganismMembershipTransition, "membership_id", nominee.pk,
            request.effective_at, request.evaluated_at,
        )
        office_head = None if office is None else _latest_transition(
            self.database_alias, ContextualRoleAssignmentStateEvent, "assignment_id", office.pk,
            request.effective_at, request.evaluated_at,
        )
        facts = {
            "m": "-" if proposer_head is None else str(proposer_head.event_uuid),
            "n": "-" if nominee_head is None else str(nominee_head.event_uuid),
            "t": proposal_route,
            "k": policy,
            "i": entitlement_reference,
        }
        if office_head is not None:
            facts["o"] = str(office_head.event_uuid)
        if configuration is not None and configuration.coordinator_nomination_policy is not None:
            facts["c"] = str(configuration.version_uuid)
        else:
            facts["d"] = "C"
        return self._qualified(request, facts)

    def _qualify_role_transition(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify ASSIGN, END or REASSIGN of a Coordinator office.

        Called by _qualify_role_proposal for those three event actions. Folds
        use request.effective_at and request.evaluated_at. In order, require:
        - LO capacity, else C_LO_NO_CAPACITY; then ACTIVE folded state:
          DORMANT is C_LO_PAUSED, CLOSED is C_LO_CLOSED, and another state
          is C_LO_NO_CAPACITY;
        - a parseable assignment_uuid, else UNRECOGNISED_ACTION;
        - an assignment and active LIVING_ORGANISM-scope COORDINATOR
          definition belonging to this LO, else DENIED;
        - for ASSIGN, its sequence-1 PROPOSE event resulting in PROPOSED,
          effective by effective_at and received and recorded by evaluated_at,
          else DENIED;
        - the holder's membership folding ACTIVE, and the assignment folding
          PROPOSED for ASSIGN or ACTIVE for END/REASSIGN, else DENIED;
        - for ASSIGN, at least one eligible Coordinator, else
          C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED;
        - the actor's membership in this LO, matching supplied locked heads
          and folding ACTIVE, else DENIED;
        - an effective Coordinator office for the actor, except for a holder
          ending their own office, else E_NOT_EFFECTIVE_COORDINATOR;
        - no effective configuration with cq_active True, else C_ROUTE_IS_CQ.

        ASSIGN then checks consent using the proposal actor read from the
        state-event table. Consent rows must be effective by effective_at
        and recorded by evaluated_at; latest means highest ordinal, with no
        separate received_at predicate. For a self-proposal, any WITHDRAW row
        refuses; otherwise the proposal itself supplies consent. For another
        proposer, the latest consent row must be CONSENT. Failure is
        C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN.

        Facts supplied to _qualified are "a" the assignment UUID, "g" the
        role UUID, "k" the action, and "o" the actor's office's current head
        when an office was required. ASSIGN adds "p" the proposal UUID and
        "u": "e:" plus that UUID for self-proposal consent, or "c:" plus the
        latest consent UUID otherwise. Consent refusals supply these facts
        without "g" and with "u" naming the latest consent row when one
        exists; on a self-proposal this need not be the WITHDRAW row itself.

        Refusals use _refuse's disclosure rule; shared fact validation and
        size rules apply. The method's own queries do not acquire command
        locks, change the assignment or write a decision.
        """
        if not self._lo_capacity(organism, request):
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        lo_state = self._lo_state(organism, request)
        if lo_state == "DORMANT":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_PAUSED)
        if lo_state == "CLOSED":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_CLOSED)
        if lo_state != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        try:
            assignment_uuid = UUID(request.target["assignment_uuid"])
        except (KeyError, TypeError, ValueError):
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        event_action = request.target.get("event_action")
        assignment = ContextualRoleAssignment.objects.using(self.database_alias).filter(
            assignment_uuid=assignment_uuid,
            membership__living_organism=organism,
            role_definition__living_organism=organism,
            role_definition__scope="LIVING_ORGANISM",
            role_definition__code="COORDINATOR",
            role_definition__active=True,
        ).select_related("membership", "role_definition").first()
        if assignment is None:
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        proposal = ContextualRoleAssignmentStateEvent.objects.using(self.database_alias).filter(
            assignment=assignment,
            sequence=1,
            action="PROPOSE",
            resulting_state="PROPOSED",
            effective_at__lte=request.effective_at,
            received_at__lte=request.evaluated_at,
            recorded_at__lte=request.evaluated_at,
        ).first()
        nominee = assignment.membership
        if event_action == "ASSIGN" and proposal is None:
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        if _fold_state(
            self.database_alias, "core_organismmembership", nominee.pk,
            request.effective_at, request.evaluated_at,
        ) != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        expected_state = "PROPOSED" if event_action == "ASSIGN" else "ACTIVE"
        if event_action not in {"ASSIGN", "END", "REASSIGN"} or _fold_state(
            self.database_alias, "core_contextualroleassignment", assignment.pk,
            request.effective_at, request.evaluated_at,
        ) != expected_state:
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        if event_action == "ASSIGN" and self._eligible_coordinator_count(organism, request) == 0:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED,
            )

        actor_membership = OrganismMembership.objects.using(self.database_alias).filter(
            identity_id=request.actor_pk,
            living_organism=organism,
        ).first()
        if actor_membership is None or not self._locked_heads_match(request, actor_membership) or _fold_state(
            self.database_alias, "core_organismmembership", actor_membership.pk,
            request.effective_at, request.evaluated_at,
        ) != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)

        holder_act = actor_membership.pk == nominee.pk
        if event_action != "END" or not holder_act:
            office = self._effective_coordinator_office(request, actor_membership)
            if office is None:
                return self._refuse(request, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
        else:
            office = None
        configuration = _effective_version(
            self.database_alias,
            organism,
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            request.effective_at,
            request.evaluated_at,
        )
        if configuration is not None and configuration.cq_active is True:
            return self._refuse(request, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)

        facts: dict[str, object] = {
            "a": str(assignment.assignment_uuid),
            "g": str(assignment.role_definition.role_uuid),
            "k": event_action,
        }
        if office is not None and office.head_state_event_id is not None:
            facts["o"] = str(office.head_state_event.event_uuid)
        if event_action == "ASSIGN":
            facts["p"] = str(proposal.event_uuid)
            with connections[self.database_alias].cursor() as cursor:
                cursor.execute(
                    "SELECT actor_id FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                    [proposal.pk],
                )
                proposal_actor_id, = cursor.fetchone()
            self_proposal = proposal_actor_id == nominee.identity_id
            consent_rows = list(OfficeAppointmentConsent.objects.using(self.database_alias).filter(
                assignment=assignment,
                proposal_event=proposal,
                nominee_membership=nominee,
                effective_at__lte=request.effective_at,
                recorded_at__lte=request.evaluated_at,
            ).order_by("ordinal"))
            if self_proposal:
                withdrawn = any(row.action == "WITHDRAW" for row in consent_rows)
                if withdrawn:
                    facts["u"] = "c:" + str(consent_rows[-1].consent_uuid)
                    facts.pop("g", None)
                    return self._refuse(
                        request,
                        S015AuthorityRefusalCode.C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN,
                        facts=facts,
                    )
                facts["u"] = "e:" + str(proposal.event_uuid)
            else:
                latest_consent = consent_rows[-1] if consent_rows else None
                if latest_consent is None or latest_consent.action != "CONSENT":
                    if latest_consent is not None:
                        facts["u"] = "c:" + str(latest_consent.consent_uuid)
                    facts.pop("g", None)
                    return self._refuse(
                        request,
                        S015AuthorityRefusalCode.C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN,
                        facts=facts,
                    )
                facts["u"] = "c:" + str(latest_consent.consent_uuid)
        return self._qualified(request, facts)

    def _qualify_office_consent(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify a nominee's CONSENT, DECLINE or WITHDRAW on a Coordinator
        proposal (RECORD_OFFICE_CONSENT).

        The dispatcher checks the event action and canonical target UUIDs.
        Folds use request.effective_at and request.evaluated_at. In order:
        - require LO capacity, else C_LO_NO_CAPACITY; then ACTIVE folded
          state: DORMANT is C_LO_PAUSED, CLOSED is C_LO_CLOSED, and another
          state is C_LO_NO_CAPACITY;
        - parse the assignment, membership and proposal UUIDs, else
          UNRECOGNISED_ACTION;
        - refuse DENIED unless the assignment and its active
          LIVING_ORGANISM-scope COORDINATOR definition belong to this LO,
          the nominee membership belongs to this LO and holds the assignment,
          the actor is that nominee, and the membership folds ACTIVE. The
          named proposal must be the assignment's sequence-1 PROPOSE event
          resulting in PROPOSED, effective by effective_at and received and
          recorded by evaluated_at;
        - refuse DENIED if an ASSIGN event is effective by effective_at and
          recorded by evaluated_at, supplying the first by sequence as "o".
          This tests event existence, not the assignment's current state;
        - require a permitted consent transition, else
          C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN.

        For the last check, read the proposal actor from the state-event table
        and prior consent rows for this assignment, proposal and nominee.
        Those rows are cut on effective_at and recorded_at, not separately
        on received_at, and latest means highest ordinal. A self-proposal
        permits only WITHDRAW with no prior row. Otherwise, no prior row
        permits CONSENT or DECLINE; a latest CONSENT permits WITHDRAW; all
        other combinations refuse.

        After nominee/proposal validation, supply "a" the assignment UUID,
        "p" the proposal UUID and, when present, "w" the latest prior consent
        UUID to later refusals. Qualified facts add "g" the role UUID, "k"
        the action and "u" the nominee's membership UUID. Refusals use
        _refuse's disclosure rule; shared fact validation and size rules apply.

        This method does not compare supplied locked heads or fold assignment
        state. Its own queries acquire no command locks and write no consent
        row or decision.
        """
        if not self._lo_capacity(organism, request):
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        lo_state = self._lo_state(organism, request)
        if lo_state == "DORMANT":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_PAUSED)
        if lo_state == "CLOSED":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_CLOSED)
        if lo_state != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        try:
            assignment_uuid = UUID(request.target["assignment_uuid"])
            membership_uuid = UUID(request.target["membership_uuid"])
            proposal_event_uuid = UUID(request.target["proposal_event_uuid"])
        except (KeyError, TypeError, ValueError):
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        assignment = ContextualRoleAssignment.objects.using(self.database_alias).filter(
            assignment_uuid=assignment_uuid,
            membership__living_organism=organism,
            role_definition__living_organism=organism,
            role_definition__scope="LIVING_ORGANISM",
            role_definition__code="COORDINATOR",
            role_definition__active=True,
        ).select_related("membership", "role_definition").first()
        nominee = OrganismMembership.objects.using(self.database_alias).filter(
            membership_uuid=membership_uuid,
            living_organism=organism,
        ).first()
        proposal = ContextualRoleAssignmentStateEvent.objects.using(self.database_alias).filter(
            event_uuid=proposal_event_uuid,
            assignment=assignment,
            sequence=1,
            action="PROPOSE",
            resulting_state="PROPOSED",
            effective_at__lte=request.effective_at,
            received_at__lte=request.evaluated_at,
            recorded_at__lte=request.evaluated_at,
        ).first() if assignment is not None else None
        if (
            assignment is None
            or nominee is None
            or assignment.membership_id != nominee.pk
            or proposal is None
            or _fold_state(
                self.database_alias, "core_organismmembership", nominee.pk,
                request.effective_at, request.evaluated_at,
            ) != "ACTIVE"
            or nominee.identity_id != request.actor_pk
        ):
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        event_action = request.target["event_action"]
        prior_rows = list(OfficeAppointmentConsent.objects.using(self.database_alias).filter(
            assignment=assignment,
            proposal_event=proposal,
            nominee_membership=nominee,
            effective_at__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        ).order_by("ordinal"))
        prior_action = prior_rows[-1].action if prior_rows else None
        decision_facts: dict[str, object] = {
            "a": str(assignment.assignment_uuid),
            "p": str(proposal.event_uuid),
        }
        if prior_rows:
            decision_facts["w"] = str(prior_rows[-1].consent_uuid)
        appointment_event = ContextualRoleAssignmentStateEvent.objects.using(self.database_alias).filter(
            assignment=assignment,
            action="ASSIGN",
            effective_at__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        ).order_by("sequence").first()
        if appointment_event is not None:
            decision_facts["o"] = str(appointment_event.event_uuid)
            return self._refuse(
                request,
                S015AuthorityRefusalCode.DENIED,
                facts=decision_facts,
            )
        with connections[self.database_alias].cursor() as cursor:
            cursor.execute(
                "SELECT actor_id FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [proposal.pk],
            )
            proposal_actor_id, = cursor.fetchone()
        self_proposal = proposal_actor_id == nominee.identity_id
        if self_proposal:
            permitted = event_action == "WITHDRAW" and prior_action is None
        elif prior_action is None:
            permitted = event_action in {"CONSENT", "DECLINE"}
        else:
            permitted = prior_action == "CONSENT" and event_action == "WITHDRAW"
        if not permitted:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN,
                facts=decision_facts,
            )
        role = assignment.role_definition
        qualified_facts: dict[str, object] = {
            **decision_facts,
            "g": str(role.role_uuid),
            "k": event_action,
            "u": str(nominee.membership_uuid),
        }
        if prior_rows:
            qualified_facts["w"] = str(prior_rows[-1].consent_uuid)
        return self._qualified(request, qualified_facts)

    def _identity_active(self, request: S015AuthorityRequest) -> bool:
        """Return whether the actor's identity is active as the row stands now.

        True when an Identity row matches the request's actor_pk,
        actor_identity_id and actor_access_epoch, its access_state is ACTIVE
        and its credential is active, and, when the request carries
        actor_credential_pk, the row's credential is that one. This is the
        current row, not a fold at the request's time cuts (compare
        _identity_active_at). Read without a lock.
        """
        identity = Identity.objects.using(self.database_alias).filter(
            pk=request.actor_pk,
            identity_id=request.actor_identity_id,
            access_epoch=request.actor_access_epoch,
            access_state=Identity.AccessState.ACTIVE,
            credential__is_active=True,
        )
        credential_pk = getattr(request, "actor_credential_pk", None)
        if credential_pk is not None:
            identity = identity.filter(credential_id=credential_pk)
        return identity.exists()

    def _identity_active_at(self, request: S015AuthorityRequest) -> bool:
        """Return whether the actor matches the identity's historical active state
        and access epoch at the request's two time cuts.

        First find the current Identity row by actor_pk and actor_identity_id.
        If actor_credential_pk is supplied, its current credential association
        must match. Then identity_state_at, with state_at=effective_at and
        known_at=evaluated_at, must report ACTIVE and actor_access_epoch.

        This does not check credential.is_active or use the Identity row's
        current access_state or access_epoch in place of the historical fold.
        Used by founding and submission. Reads without explicit row locks.
        """
        identity = Identity.objects.using(self.database_alias).filter(
            pk=request.actor_pk,
            identity_id=request.actor_identity_id,
        ).first()
        if identity is None:
            return False
        credential_pk = getattr(request, "actor_credential_pk", None)
        if credential_pk is not None and identity.credential_id != credential_pk:
            return False
        state = identity_state_at(
            request.actor_identity_id,
            state_at=request.effective_at,
            known_at=request.evaluated_at,
            database_alias=self.database_alias,
        )
        return (
            state.access_state == Identity.AccessState.ACTIVE
            and state.access_epoch == request.actor_access_epoch
        )

    def _registered_reference_uuid(
        self,
        identity_id: UUID,
        reference: object,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> UUID | None:
        """Return the registration UUID a registration reference names, or None.

        reference must be a str of exactly "learner-registration:" followed by
        the UUID's canonical form (lower case, hyphenated, as str(UUID) writes
        it); an upper-case, braced or "urn:" form, a different prefix or a
        non-UUID gives None. The UUID must then name a LearnerRegistration of
        identity_id with effective_at at or before effective_at and received_at
        and recorded_at at or before evaluated_at; otherwise None.

        Postcondition relied on by its callers (_evaluate_founding, _submit):
        a UUID is returned only for a canonical reference to an eligible
        registration of this identity. Since U-TIDY-1 (GF-1) this is the only
        check of the reference's canonical form. Read without a lock.
        """
        prefix = "learner-registration:"
        if type(reference) is not str or not reference.startswith(prefix):
            return None
        value = reference.removeprefix(prefix)
        try:
            registration_uuid = UUID(value)
        except (TypeError, ValueError):
            return None
        if reference != f"{prefix}{registration_uuid}":
            return None
        exists = LearnerRegistration.objects.using(self.database_alias).filter(
            registration_uuid=registration_uuid,
            identity__identity_id=identity_id,
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).exists()
        return registration_uuid if exists else None

    def _refuse(
        self,
        request: S015AuthorityRequest,
        code: S015AuthorityRefusalCode,
        disclosed: S015AuthorityRefusalCode | None = None,
        *,
        facts: dict[str, object] | None = None,
    ) -> S015AuthorityResult:
        """Build the refusal of a request with the given code.

        disclosed, when None, is the code itself for the codes an applicant
        or an outsider may learn and for DENIED, and otherwise the code for
        a current member or DENIED for anyone else. facts are the auxiliary
        facts reported beside x; they must not carry x and must be declared
        by the action's refusal shape for the request's form
        (core.organism_authority_facts), or S015AuthorityFactsUndeclared is
        raised. The reference is "s015a1:" plus the canonical JSON of
        {"x": code} with the facts; a reference over the 255-character cap
        keeps its code and drops the auxiliary facts, so a governed refusal
        code is never replaced by the size refusal. Returns the request
        type's refusal carrier.
        """
        if disclosed is None:
            if code in {
                S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN,
                S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                S015AuthorityRefusalCode.E_NOT_APPLICANT,
                S015AuthorityRefusalCode.E_NOT_REGISTERED,
                S015AuthorityRefusalCode.E_NOT_ASSIGNEE,
                S015AuthorityRefusalCode.C_TERMINAL_ROW_STANDS,
                S015AuthorityRefusalCode.C_APPLICATION_OPEN,
                S015AuthorityRefusalCode.UNRECOGNISED_ACTION,
                S015AuthorityRefusalCode.UNRECOGNISED_ROUTE,
            }:
                disclosed = code
            elif code is S015AuthorityRefusalCode.DENIED:
                disclosed = code
            else:
                disclosed = code if self._current_member(request) else S015AuthorityRefusalCode.DENIED
        fields = asdict(request)
        fields["refusal_code"] = code
        fields["disclosed_refusal_code"] = disclosed
        if facts and S015_AUTHORITY_REFUSAL_KEY in facts:
            raise S015AuthorityFactsUndeclared(
                f"{S015_AUTHORITY_FACTS_SCHEMA}: {request.action.value} refusal facts must not carry "
                f"{S015_AUTHORITY_REFUSAL_KEY!r}"
            )
        authority_facts: dict[str, object] = {S015_AUTHORITY_REFUSAL_KEY: code.value}
        if facts:
            authority_facts.update(facts)
        authority_reference = self._encode_facts(request, authority_facts, refusal_code=code.value)
        if len(authority_reference) > S015_AUTHORITY_REFERENCE_MAX_LENGTH:
            authority_reference = self._encode_facts(
                request, {S015_AUTHORITY_REFUSAL_KEY: code.value}, refusal_code=code.value,
            )
        fields["authority_reference"] = authority_reference
        if type(request) is S015PreFoundingAuthorityRequest:
            return S015PreFoundingAuthorityRefusal(**fields)
        if type(request) is S015OwnApplicantAuthorityRequest:
            return S015OwnApplicantAuthorityRefusal(**fields)
        return S015PostFoundingAuthorityRefusal(**fields)

    def _current_member(self, request: S015AuthorityRequest) -> bool:
        """Return whether the actor is a current member of the request's LO.

        True when the request names an LO and an effective_at, the LO exists,
        the actor has a membership in it, and that membership folds ACTIVE or
        PROBATIONARY at effective_at and evaluated_at (_fold_state); otherwise
        False. _refuse uses it to decide whether a refusal code is disclosed
        or reported as DENIED. Read without a lock.
        """
        organism_id = getattr(request, "living_organism_id", None)
        effective_at = getattr(request, "effective_at", None)
        if organism_id is None or effective_at is None:
            return False
        organism = LivingOrganism.objects.using(self.database_alias).filter(organism_id=organism_id).first()
        if organism is None:
            return False
        membership = OrganismMembership.objects.using(self.database_alias).filter(
            identity_id=request.actor_pk,
            living_organism=organism,
        ).first()
        if membership is None:
            return False
        state = _fold_state(
            self.database_alias,
            "core_organismmembership",
            membership.pk,
            effective_at,
            request.evaluated_at,
        )
        return state in {"ACTIVE", "PROBATIONARY"}

    def _lo_state(self, organism: LivingOrganism, request: S015AuthorityRequest) -> str | None:
        """Return the LO's state folded at the request's effective_at and
        evaluated_at (_fold_state), or None when no LO event is eligible."""
        return _fold_state(
            self.database_alias,
            "core_livingorganism",
            organism.pk,
            request.effective_at,
            request.evaluated_at,
        )

    def _submit(
        self,
        request: S015OwnApplicantAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify an applicant's SUBMIT_APPLICATION to the LO.

        In order, at the time cuts:
        - the Birthing source returns a non-empty str, else C_GATEWAY_NOT_OPEN
          with fact "b" None;
        - the LO folds ACTIVE, else C_GATEWAY_NOT_OPEN;
        - a governance configuration is in effect, else
          C_CONFIGURATION_NOT_ESTABLISHED, disclosed as C_GATEWAY_NOT_OPEN;
        - application settings and an application question are in effect and
          the settings' applications_open is True, else C_GATEWAY_NOT_OPEN;
        - the registration source returns a reference and
          _registered_reference_uuid resolves it to an eligible registration of
          the actor, else E_NOT_REGISTERED;
        - the actor's identity is active at the time cuts
          (_identity_active_at), else E_IDENTITY_INACTIVE;
          evaluate_organism_authority does not apply _identity_active here;
        - the actor is not a current member: a membership in this LO folding
          ACTIVE or PROBATIONARY is DENIED, and one in any other state does
          not stop an application;
        - the actor has no open application to this LO, one with neither a
          decision nor a withdrawal, else C_APPLICATION_OPEN;
        - a predecessor_reference, when given, names the actor's own
          application to this LO that has been withdrawn, else DENIED.

        Applications, decisions and withdrawals are cut on effective_at and
        recorded_at, not received_at, so a decision or withdrawal effective
        after effective_at leaves the application open here. Each code is
        disclosed as itself except as stated. Qualified facts: "b" the
        Birthing reference, "c", "q" and "s" the configuration, question and
        settings versions, and "r" the registration UUID. Reads only.
        """
        effective_at = request.effective_at
        evaluated_at = request.evaluated_at
        birthing_reference = self.birthing_source.reference_at(
            organism.organism_id, effective_at, evaluated_at,
        )
        if type(birthing_reference) is not str or not birthing_reference:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN,
                facts={"b": None},
            )
        if self._lo_state(organism, request) != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN)
        configuration = _effective_version(
            self.database_alias, organism, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            effective_at, evaluated_at,
        )
        settings = _effective_version(
            self.database_alias, organism, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            effective_at, evaluated_at,
        )
        question = _effective_version(
            self.database_alias, organism, OrganismAction.EFFECT_APPLICATION_QUESTION,
            effective_at, evaluated_at,
        )
        if configuration is None:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.C_CONFIGURATION_NOT_ESTABLISHED,
                S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN,
            )
        if settings is None or question is None or settings.applications_open is not True:
            return self._refuse(request, S015AuthorityRefusalCode.C_GATEWAY_NOT_OPEN)
        registration_reference = self.registration_source.reference_at(
            request.actor_identity_id, effective_at, evaluated_at,
        )
        if not registration_reference:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_NOT_REGISTERED,
                S015AuthorityRefusalCode.E_NOT_REGISTERED,
            )
        registration_uuid = self._registered_reference_uuid(
            request.actor_identity_id,
            registration_reference,
            effective_at,
            evaluated_at,
        )
        if registration_uuid is None:
            return self._refuse(request, S015AuthorityRefusalCode.E_NOT_REGISTERED)
        if not self._identity_active_at(request):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
            )
        membership = OrganismMembership.objects.using(self.database_alias).filter(
            identity_id=request.actor_pk,
            living_organism=organism,
        ).first()
        if membership is not None:
            member_state = _fold_state(
                self.database_alias, "core_organismmembership", membership.pk,
                effective_at, evaluated_at,
            )
            if member_state in {"ACTIVE", "PROBATIONARY"}:
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        open_applications = Application.objects.using(self.database_alias).filter(
            applicant_id=request.actor_pk,
            living_organism=organism,
            effective_at__lte=effective_at,
            recorded_at__lte=evaluated_at,
        )
        for application in open_applications:
            decided = ApplicationDecision.objects.using(self.database_alias).filter(
                application=application,
                effective_at__lte=effective_at,
                recorded_at__lte=evaluated_at,
            ).exists()
            withdrawn = ApplicationWithdrawal.objects.using(self.database_alias).filter(
                application=application,
                effective_at__lte=effective_at,
                recorded_at__lte=evaluated_at,
            ).exists()
            if not decided and not withdrawn:
                return self._refuse(request, S015AuthorityRefusalCode.C_APPLICATION_OPEN)
        if request.predecessor_reference is not None:
            predecessor = Application.objects.using(self.database_alias).filter(
                application_uuid=request.predecessor_reference,
                applicant_id=request.actor_pk,
                living_organism=organism,
                effective_at__lte=effective_at,
                recorded_at__lte=evaluated_at,
            ).first()
            eligible = predecessor is not None and ApplicationWithdrawal.objects.using(
                self.database_alias,
            ).filter(
                application=predecessor,
                effective_at__lte=effective_at,
                recorded_at__lte=evaluated_at,
            ).exists()
            if not eligible:
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        facts = {
            "b": birthing_reference,
            "c": str(configuration.version_uuid),
            "q": str(question.version_uuid),
            "r": str(registration_uuid),
            "s": str(settings.version_uuid),
        }
        return self._qualified(request, facts)

    def _withdraw(
        self,
        request: S015OwnApplicantAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify an applicant's WITHDRAW_APPLICATION.

        In order:
        - request.application_pk names an application by the actor to this
          LO, cut on effective_at and recorded_at, else E_NOT_APPLICANT
          disclosed as DENIED;
        - target["application_uuid"] equals its UUID, else E_NOT_APPLICANT
          disclosed as DENIED;
        - no decision and no withdrawal of it is recorded by evaluated_at,
          whatever its effective_at, else C_TERMINAL_ROW_STANDS;
        - its latest engagement (highest engagement_ordinal, cut on
          effective_at and recorded_at), when there is one, is on route LO_CSA
          or LO_AUTOMATIC; LO_CQ, CIRCLE_CQ or any other route is
          UNRECOGNISED_ROUTE.

        C_TERMINAL_ROW_STANDS and UNRECOGNISED_ROUTE are disclosed as
        themselves. Qualified facts: "a" the application UUID and "t" the
        engagement's route, or "NONE" when there is no engagement. Reads only.
        """
        application = Application.objects.using(self.database_alias).filter(
            pk=request.application_pk,
            applicant_id=request.actor_pk,
            living_organism=organism,
            effective_at__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        ).first()
        if application is None:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_NOT_APPLICANT,
                S015AuthorityRefusalCode.DENIED,
            )
        if request.target.get("application_uuid") != application.application_uuid:
            return self._refuse(request, S015AuthorityRefusalCode.E_NOT_APPLICANT, S015AuthorityRefusalCode.DENIED)
        if ApplicationDecision.objects.using(self.database_alias).filter(
            application=application,
            recorded_at__lte=request.evaluated_at,
        ).exists():
            return self._refuse(request, S015AuthorityRefusalCode.C_TERMINAL_ROW_STANDS)
        if ApplicationWithdrawal.objects.using(self.database_alias).filter(
            application=application,
            recorded_at__lte=request.evaluated_at,
        ).exists():
            return self._refuse(request, S015AuthorityRefusalCode.C_TERMINAL_ROW_STANDS)
        engagement = ApplicationEngagement.objects.using(self.database_alias).filter(
            application=application,
            effective_at__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        ).order_by("-engagement_ordinal").first()
        if engagement is not None and engagement.route in {"LO_CQ", "CIRCLE_CQ"}:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ROUTE)
        if engagement is not None and engagement.route not in {"LO_CSA", "LO_AUTOMATIC"}:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ROUTE)
        facts = {
            "a": str(application.application_uuid),
            "t": "NONE" if engagement is None else engagement.route,
        }
        return self._qualified(request, facts)

    def _coordinator_act(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Route authority checks for acts not dispatched to another qualifier.

        These include gateway versions, phase advance, Birthing, entitlement
        election, governance formation and application processing. LO and
        membership folds use request.effective_at and request.evaluated_at.

        REQUEST_GOVERNANCE_SEAT refuses DORMANT or CLOSED as
        SBG_LO_NOT_ACTIVE, otherwise delegates to
        _qualify_governance_seat_request without the later capacity,
        membership or office gates in this router. The same two states also
        refuse ADOPT_GOVERNING_INSTRUMENT, CREATE_DORMANT_CIRCLE and
        ADMIT_CIRCLE_PARTICIPANT as SBG_LO_NOT_ACTIVE.

        For remaining cases, check capacity first (C_LO_NO_CAPACITY), then
        read the effective governance configuration. DORMANT is C_LO_PAUSED
        and CLOSED is C_LO_CLOSED. Another non-ACTIVE state is
        C_LO_NO_CAPACITY unless this is the founder's first
        EFFECT_GOVERNANCE_CONFIGURATION with no configuration in effect,
        or, while FOUNDING_PENDING, a gateway-version act with a configuration
        in effect or one of the three formation acts above. These exceptions
        do not bypass the earlier capacity or DORMANT/CLOSED checks.

        Next require the actor's membership in this LO, matching supplied
        locked heads and folding ACTIVE, else E_NOT_EFFECTIVE_COORDINATOR.
        Route as follows, treating a Birthing-source result of None as
        pre-Birthing and any other result as post-Birthing:
        - Pre-Birthing ADMIT_CIRCLE_PARTICIPANT requires the founder, else
          DENIED, and route CIRCLE_FOUNDING, else SBG_SEAT_ROUTE_UNAVAILABLE;
          delegate to _qualify_governance_founding_seating without an office
          requirement. Post-Birthing, refuse CIRCLE_FOUNDING with the same
          route code, require an effective Coordinator office, and delegate
          to _qualify_governance_constitutional_seating.
        - Pre-Birthing ADOPT_GOVERNING_INSTRUMENT and CREATE_DORMANT_CIRCLE
          require the founder, else DENIED, but do not require an office;
          delegate to _qualify_governance_adoption or
          _qualify_governance_circle_creation.
        - All other cases require an effective Coordinator office, then use
          _qualify_phase_advance or _qualify_birthing for the corresponding
          TRANSITION_LIVING_ORGANISM subtype; _qualify_birthing also handles
          EFFECT_ENTITLEMENT_ELECTION whose subtype equals the action and
          whose target has type exactly dict and exactly the keys action,
          organism_uuid and snapshot_uuid. Otherwise dispatch adoption or
          circle creation to their qualifiers, version actions to
          _qualify_gateway_change, and the remainder to
          _qualify_application_processing.

        A required office being absent is E_NOT_EFFECTIVE_COORDINATOR.
        Local refusals use _refuse's disclosure rule; delegated qualifiers
        supply their own results and facts. The router's own queries acquire
        no command locks and write no governed records or decisions.
        """
        effective_at = request.effective_at
        evaluated_at = request.evaluated_at
        lo_state = self._lo_state(organism, request)
        if request.action is OrganismAction.REQUEST_GOVERNANCE_SEAT:
            if lo_state in {"DORMANT", "CLOSED"}:
                return self._refuse(request, S015AuthorityRefusalCode.SBG_LO_NOT_ACTIVE)
            return self._qualify_governance_seat_request(request, organism)
        if (
            request.action in {
                OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
                OrganismAction.CREATE_DORMANT_CIRCLE,
                OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
            }
            and lo_state in {"DORMANT", "CLOSED"}
        ):
            return self._refuse(request, S015AuthorityRefusalCode.SBG_LO_NOT_ACTIVE)
        if not self._lo_capacity(organism, request):
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        current_configuration = _effective_version(
            self.database_alias, organism, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            effective_at, evaluated_at,
        )
        first_configuration = (
            request.action is OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION
            and current_configuration is None
            and organism.founder_identity_id == request.actor_pk
        )
        formation_gateway_change = (
            lo_state == "FOUNDING_PENDING"
            and request.action in _VERSION_SPECS
            and current_configuration is not None
        )
        formation_governance_act = (
            lo_state == "FOUNDING_PENDING"
            and request.action in {
                OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
                OrganismAction.CREATE_DORMANT_CIRCLE,
                OrganismAction.ADMIT_CIRCLE_PARTICIPANT,
            }
        )
        if lo_state == "DORMANT":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_PAUSED)
        if lo_state == "CLOSED":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_CLOSED)
        if (
            lo_state != "ACTIVE"
            and not first_configuration
            and not formation_gateway_change
            and not formation_governance_act
        ):
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)

        membership = OrganismMembership.objects.using(self.database_alias).filter(
            identity_id=request.actor_pk,
            living_organism=organism,
        ).first()
        if membership is None or not self._locked_heads_match(request, membership):
            return self._refuse(request, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
        if _fold_state(
            self.database_alias, "core_organismmembership", membership.pk,
            effective_at, evaluated_at,
        ) != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
        if request.action is OrganismAction.ADMIT_CIRCLE_PARTICIPANT:
            birthing_reference = self.birthing_source.reference_at(
                organism.organism_id,
                effective_at,
                evaluated_at,
            )
            target = request.target or {}
            if birthing_reference is None:
                if (
                    organism.founder_identity_id != request.actor_pk
                    or lo_state in {"DORMANT", "CLOSED"}
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.DENIED)
                if target.get("route") != "CIRCLE_FOUNDING":
                    return self._refuse(
                        request,
                        S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE,
                    )
                return self._qualify_governance_founding_seating(request, organism)
            if target.get("route") == "CIRCLE_FOUNDING":
                return self._refuse(
                    request,
                    S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE,
                )
            office = self._effective_coordinator_office(request, membership)
            if office is None:
                return self._refuse(
                    request,
                    S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR,
                )
            return self._qualify_governance_constitutional_seating(request, organism)
        if request.action in {
            OrganismAction.ADOPT_GOVERNING_INSTRUMENT,
            OrganismAction.CREATE_DORMANT_CIRCLE,
        } and self.birthing_source.reference_at(
            organism.organism_id,
            effective_at,
            evaluated_at,
        ) is None:
            if organism.founder_identity_id != request.actor_pk:
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
            if request.action is OrganismAction.ADOPT_GOVERNING_INSTRUMENT:
                return self._qualify_governance_adoption(request, organism)
            return self._qualify_governance_circle_creation(request, organism)
        office = self._effective_coordinator_office(request, membership)
        if office is None:
            return self._refuse(request, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)

        if request.command_subtype == (
            f"{OrganismAction.TRANSITION_LIVING_ORGANISM.value}/PHASE_ADVANCE"
        ):
            return self._qualify_phase_advance(request, organism, office)
        if request.command_subtype == (
            f"{OrganismAction.TRANSITION_LIVING_ORGANISM.value}/BIRTHING"
        ):
            return self._qualify_birthing(request, organism, office)
        if (
            request.action is OrganismAction.EFFECT_ENTITLEMENT_ELECTION
            and request.command_subtype == request.action.value
            and type(request.target) is dict
            and set(request.target) == {"action", "organism_uuid", "snapshot_uuid"}
        ):
            return self._qualify_birthing(request, organism, office)
        if request.action is OrganismAction.ADOPT_GOVERNING_INSTRUMENT:
            return self._qualify_governance_adoption(request, organism)
        if request.action is OrganismAction.CREATE_DORMANT_CIRCLE:
            return self._qualify_governance_circle_creation(request, organism)
        if request.action in _VERSION_SPECS:
            return self._qualify_gateway_change(request, organism, membership, office)
        return self._qualify_application_processing(request, organism, membership, office)

    def _qualify_phase_advance(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
        office: ContextualRoleAssignment,
    ) -> S015AuthorityResult:
        """Qualify a pre-Birthing PHASE_ADVANCE of the LO.

        The dispatcher checks the target keys and string phases; the
        router requires an effective Coordinator office. A missing
        target is UNRECOGNISED_ACTION, a defensive guard.

        Read BIRTHING and the latest PHASE_ADVANCE events effective
        by request.effective_at and received and recorded by
        evaluated_at. Select the latest phase by effective_at,
        occurred_at, received_at, recorded_at, sequence and
        event_uuid, descending. A missing phase reference means
        CONCEIVING; otherwise parse after the last colon and the
        first arrow. This is not format validation: a malformed
        non-null reference can raise IndexError.

        In order, refuse:
        - PHASE_OUT_OF_ORDER when Birthing is present at those cuts,
          from_phase differs from the parsed phase, or the step is
          neither CONCEIVING->GERMINATING nor GERMINATING->BIRTHING;
        - INV_CQ_ROUTE_REQUIRED when more than one membership folds
          ACTIVE at the cuts. PROBATIONARY is not counted; this
          helper has no separate zero-count refusal;
        - PHASE_CONDITION_UNMET for GERMINATING unless
          founding-content version 1, effective and recorded at the
          cuts, has a non-blank name and purpose;
        - PHASE_CONDITION_UNMET for BIRTHING unless an effective
          governance configuration has a non-null governance_enabled
          choice. If enabled, return any PHASE_ADVANCE readiness
          refusal.

        The first two refusals supply "p" as FROM->TO. Condition
        failures supply "p" as "founding content (name and purpose)",
        "an effective governance configuration" or "GOVERNANCE
        choice". Readiness refusals supply no auxiliary facts here.

        Qualified facts are "kind": PHASE_ADVANCE; "c": the
        configuration UUID, or "-" for GERMINATING; "o": the office
        assignment UUID; "f": FORMATION_RULES:PLATFORM_DEFAULT@v1;
        and "p": FROM->TO. Refusals use _refuse's disclosure rule.
        This method reads without acquiring command locks or writing
        a phase event.
        """
        target = request.target
        if target is None:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        from_phase = target["from_phase"]
        to_phase = target["to_phase"]

        birthing_recorded = LivingOrganismEvent.objects.using(self.database_alias).filter(
            living_organism=organism,
            action="BIRTHING",
            effective_at__lte=request.effective_at,
            received_at__lte=request.evaluated_at,
            recorded_at__lte=request.evaluated_at,
        ).exists()
        latest_phase_reference = LivingOrganismEvent.objects.using(self.database_alias).filter(
            living_organism=organism,
            action="PHASE_ADVANCE",
            effective_at__lte=request.effective_at,
            received_at__lte=request.evaluated_at,
            recorded_at__lte=request.evaluated_at,
        ).order_by(
            "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
        ).values_list("evidence_reference", flat=True).first()
        current_phase = (
            "CONCEIVING"
            if latest_phase_reference is None
            else latest_phase_reference.rsplit(":", 1)[1].split("->", 1)[1]
        )
        if (
            birthing_recorded
            or from_phase != current_phase
            or (from_phase, to_phase) not in {
                ("CONCEIVING", "GERMINATING"),
                ("GERMINATING", "BIRTHING"),
            }
        ):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.PHASE_OUT_OF_ORDER,
                facts={"p": f"{from_phase}->{to_phase}"},
            )

        with connections[self.database_alias].cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.core_organismmembership m "
                "WHERE m.living_organism_id = %s "
                "AND public.s015_fold_effective_state("
                "'core_organismmembership', m.id, %s, %s) = 'ACTIVE'",
                [organism.pk, request.effective_at, request.evaluated_at],
            )
            electorate_count = cursor.fetchone()[0]
        if electorate_count > 1:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED,
                facts={"p": f"{from_phase}->{to_phase}"},
            )

        configuration = None
        if to_phase == "GERMINATING":
            content = LOFoundingContentVersion.objects.using(self.database_alias).filter(
                living_organism=organism,
                version=1,
                effective_at__lte=request.effective_at,
                recorded_at__lte=request.evaluated_at,
            ).first()
            if content is None or not content.name.strip() or not content.purpose.strip():
                return self._refuse(
                    request,
                    S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                    facts={"p": "founding content (name and purpose)"},
                )
        else:
            configuration = _effective_version(
                self.database_alias,
                organism,
                OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                request.effective_at,
                request.evaluated_at,
            )
            if configuration is None:
                return self._refuse(
                    request,
                    S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                    facts={"p": "an effective governance configuration"},
                )
            if configuration.governance_enabled is None:
                return self._refuse(
                    request,
                    S015AuthorityRefusalCode.PHASE_CONDITION_UNMET,
                    facts={"p": "GOVERNANCE choice"},
                )
            if configuration.governance_enabled:
                readiness_refusal = self._sbg_governance_readiness(
                    organism.pk,
                    request.effective_at,
                    request.evaluated_at,
                    "PHASE_ADVANCE",
                )
                if readiness_refusal is not None:
                    return self._refuse(request, readiness_refusal)

        return self._qualified(request, {
            "kind": "PHASE_ADVANCE",
            "c": "-" if configuration is None else str(configuration.version_uuid),
            "o": str(office.assignment_uuid),
            "f": "FORMATION_RULES:PLATFORM_DEFAULT@v1",
            "p": f"{from_phase}->{to_phase}",
        })

    def _qualify_birthing(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
        office: ContextualRoleAssignment,
    ) -> S015AuthorityResult:
        """Qualify Birthing, including the snapshot-form entitlement
        election.

        The router supplies an effective Coordinator office for
        TRANSITION_LIVING_ORGANISM/BIRTHING and for the snapshot form
        of EFFECT_ENTITLEMENT_ELECTION. The dispatcher checks the
        snapshot UUID's canonical form; this helper does not load its
        snapshot row.

        At request.effective_at and request.evaluated_at, check in
        order:
        - LO capacity and ACTIVE folded state, else C_LO_NO_CAPACITY;
        - no BIRTHING event effective by effective_at and received
          and recorded by evaluated_at, else
          BIRTHING_ALREADY_RECORDED;
        - the latest PHASE_ADVANCE event at those same cuts has
          reference formation-phase:GERMINATING->BIRTHING, else
          BIRTHING_PREREQUISITE_MISSING with "p": "phase";
        - exactly one membership folds ACTIVE: zero is
          SB_BIRTHING_ELECTORATE_EMPTY, more than one is
          INV_CQ_ROUTE_REQUIRED. PROBATIONARY is not counted;
        - an effective governance configuration with a non-null
          governance_enabled choice, else
          BIRTHING_PREREQUISITE_MISSING with "p": "an effective
          governance configuration". If enabled, return any BIRTHING
          readiness refusal;
        - effective application settings with non-null
          applications_open and automatic_acceptance, else
          BIRTHING_PREREQUISITE_MISSING with "p": "both application
          settings chosen";
        - an effective application question, else the same
          prerequisite code with "p": "a reviewed application
          question";
        - a target, else UNRECOGNISED_ACTION, a defensive guard.

        Latest phase ordering is effective_at, occurred_at,
        received_at, recorded_at, sequence and event_uuid,
        descending. These checks require the settings' choices, not
        that either choice is True.

        Qualified facts are "kind": BIRTHING; "c", "s" and "q": the
        configuration, settings and question UUIDs; "o": the office
        assignment UUID; and "n": snapshot_uuid copied from the
        target. No snapshot contents are validated here. Refusals use
        _refuse's disclosure rule. This method reads without
        acquiring command locks, writing a Birthing event or granting
        an entitlement.
        """
        effective_at = request.effective_at
        evaluated_at = request.evaluated_at
        if not self._lo_capacity(organism, request) or self._lo_state(organism, request) != "ACTIVE":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)

        if LivingOrganismEvent.objects.using(self.database_alias).filter(
            living_organism=organism,
            action="BIRTHING",
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).exists():
            return self._refuse(request, S015AuthorityRefusalCode.BIRTHING_ALREADY_RECORDED)

        phase_reference = LivingOrganismEvent.objects.using(self.database_alias).filter(
            living_organism=organism,
            action="PHASE_ADVANCE",
            effective_at__lte=effective_at,
            received_at__lte=evaluated_at,
            recorded_at__lte=evaluated_at,
        ).order_by(
            "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
        ).values_list("evidence_reference", flat=True).first()
        if phase_reference != "formation-phase:GERMINATING->BIRTHING":
            return self._refuse(
                request,
                S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
                facts={"p": "phase"},
            )

        with connections[self.database_alias].cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.core_organismmembership m "
                "WHERE m.living_organism_id = %s "
                "AND public.s015_fold_effective_state("
                "'core_organismmembership', m.id, %s, %s) = 'ACTIVE'",
                [organism.pk, effective_at, evaluated_at],
            )
            electorate_count = cursor.fetchone()[0]
        if electorate_count == 0:
            return self._refuse(request, S015AuthorityRefusalCode.SB_BIRTHING_ELECTORATE_EMPTY)
        if electorate_count > 1:
            return self._refuse(request, S015AuthorityRefusalCode.INV_CQ_ROUTE_REQUIRED)

        configuration = _effective_version(
            self.database_alias,
            organism,
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            effective_at,
            evaluated_at,
        )
        if configuration is None:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
                facts={"p": "an effective governance configuration"},
            )
        if configuration.governance_enabled is None:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
                facts={"p": "an effective governance configuration"},
            )
        if configuration.governance_enabled:
            readiness_refusal = self._sbg_governance_readiness(
                organism.pk,
                effective_at,
                evaluated_at,
                "BIRTHING",
            )
            if readiness_refusal is not None:
                return self._refuse(request, readiness_refusal)

        settings = _effective_version(
            self.database_alias,
            organism,
            OrganismAction.EFFECT_APPLICATION_SETTINGS,
            effective_at,
            evaluated_at,
        )
        if (
            settings is None
            or settings.applications_open is None
            or settings.automatic_acceptance is None
        ):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
                facts={"p": "both application settings chosen"},
            )
        question = _effective_version(
            self.database_alias,
            organism,
            OrganismAction.EFFECT_APPLICATION_QUESTION,
            effective_at,
            evaluated_at,
        )
        if question is None:
            return self._refuse(
                request,
                S015AuthorityRefusalCode.BIRTHING_PREREQUISITE_MISSING,
                facts={"p": "a reviewed application question"},
            )

        target = request.target
        if target is None:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        snapshot_uuid = target["snapshot_uuid"]
        facts = {
            "kind": "BIRTHING",
            "c": str(configuration.version_uuid),
            "s": str(settings.version_uuid),
            "q": str(question.version_uuid),
            "o": str(office.assignment_uuid),
            "n": snapshot_uuid,
        }
        return self._qualified(request, facts)

    def _qualify_governance_adoption(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify adopting the LO's governing instrument
        (ADOPT_GOVERNING_INSTRUMENT, subtype GOVERNANCE_CONSTITUTION).

        Reached from _coordinator_act: before Birthing by the founder, with
        no office required; after Birthing with an effective Coordinator
        office. evaluate_organism_authority has checked the target's key
        set, a canonical rule_uuid, a version of at least 1, a str source
        and a dict body. A missing target is UNRECOGNISED_ACTION (a guard).
        The target's source must be FOUNDING_BODY_PRESCRIPTION while the
        Birthing source returns None, and LO_PRESCRIPTION once it returns a
        reference, else DENIED.

        Qualified facts: "kind" GOVERNANCE_CONSTITUTION, "g" the target's
        rule_uuid and "source". The body, version and effective_from are not
        checked here. Reads only.
        """
        target = request.target
        if target is None:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        birthing_reference = self.birthing_source.reference_at(
            organism.organism_id,
            request.effective_at,
            request.evaluated_at,
        )
        expected_source = (
            "LO_PRESCRIPTION" if birthing_reference is not None else "FOUNDING_BODY_PRESCRIPTION"
        )
        if target.get("source") != expected_source:
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        return self._qualified(request, {
            "kind": "GOVERNANCE_CONSTITUTION",
            "g": target["rule_uuid"],
            "source": expected_source,
        })

    def _qualify_governance_founding_seating(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify the founder seating themself on the Governance Circle
        before Birthing (ADMIT_CIRCLE_PARTICIPANT, route CIRCLE_FOUNDING).

        Reached from _coordinator_act for the founder only, before Birthing,
        with no office required. UNRECOGNISED_ACTION when there is no target,
        or the subtype is not ADMIT_CIRCLE_PARTICIPANT, or the target's
        action, organism_uuid, kind (GOVERNANCE) or route (CIRCLE_FOUNDING)
        does not match, or its applicant_identity_uuid is not the actor's.
        The seat request the target names must be a fact at the time cuts
        (_governance_request_matches_target, with its same-request rule for
        a compound seating), else SBG_MEMBER_NOT_ELIGIBLE.

        Qualified facts: "kind" GOVERNANCE, "route" CIRCLE_FOUNDING, and "c",
        "r", "s" and "q" the target's circle_uuid, seat_rule_uuid, seat_id
        and request_uuid. Codes are disclosed by _refuse's rule. Reads only.
        """
        target = request.target
        if target is None:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        if (
            request.command_subtype
            != OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value
            or target.get("action") != OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value
            or target.get("organism_uuid") != organism.organism_id
            or target.get("kind") != "GOVERNANCE"
            or target.get("route") != "CIRCLE_FOUNDING"
            or target.get("applicant_identity_uuid") != str(request.actor_identity_id)
        ):
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        if not self._governance_request_matches_target(request, organism, target):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
            )
        return self._qualified(request, {
            "kind": "GOVERNANCE",
            "route": "CIRCLE_FOUNDING",
            "c": target["circle_uuid"],
            "r": target["seat_rule_uuid"],
            "s": target["seat_id"],
            "q": target["request_uuid"],
        })

    def _governance_request_matches_target(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
        target: dict[str, object],
    ) -> bool:
        """Report whether the seat request the target names is a fact at the
        request's time coordinates.

        The ordinary read admits a CircleJoinRequest of this organism, circle,
        applicant, seat rule and seat, initiated by the applicant, effective
        no later than effective_at and received and recorded no later than
        evaluated_at: the same time cut every other authority fact observes.

        One row is read under a different cut. A compound governance seating
        runs its two stages at the parent intake's single time T, and its
        stage 1 inserts the seat request with received_at = T and a
        database-assigned recorded_at that is later than T. The same-request
        rule (_same_request_stage_row) admits exactly that row, by proving it
        is this request's own first-stage write; no other row escapes the
        recorded_at cut, and no refusal code changes.
        """
        candidates = CircleJoinRequest.objects.using(self.database_alias).filter(
            request_uuid=target["request_uuid"],
            circle__parent_organism=organism,
            circle__circle_uuid=target["circle_uuid"],
            membership__identity__identity_id=target["applicant_identity_uuid"],
            seat_rule_uuid=target["seat_rule_uuid"],
            seat_id=target["seat_id"],
            initiated_by="APPLICANT",
            effective_at__lte=request.effective_at,
            received_at__lte=request.evaluated_at,
        )
        if candidates.filter(recorded_at__lte=request.evaluated_at).exists():
            return True
        return self._same_request_stage_row(request, organism, target, candidates)

    def _same_request_stage_row(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
        target: dict[str, object],
        candidates: QuerySet[CircleJoinRequest],
    ) -> bool:
        """The same-request rule: admit the seat request this compound
        seating's own stage 1 wrote at the same time T, whatever its
        recorded_at.

        The request's own fields (idempotency key, request reference,
        actor, evaluated_at, the target's request_uuid) are each compared
        with durable rows or with values derived by the production rule;
        nothing is admitted on the request's say-so. True only when all of
        these hold:

        0. pre-filter: the request's idempotency key carries the derived
           admission-stage prefix (_COMPOUND_ADMISSION_KEY_PREFIX, the
           literal _compound_stage_identifiers uses); implied by 1, it spares
           every other request the statement below;
        1. parent association: among the command-intake rows binding this
           actor (actor_id and actor_identity_id), a compound seating entry,
           the admission action, this organism as target and received_at =
           evaluated_at, exactly one has an interaction key whose derived
           admission key and reference equal the request's idempotency key
           and request reference;
        2. identity of the request: the target's request_uuid equals the
           stage-1 identity derived from that parent, uuid5(organism_uuid,
           "REQUEST_GOVERNANCE_SEAT:" + derived request key), the value
           stage 1 computes;
        3. the row: a candidate (the ordinary filters without the recorded_at
           cut) with that identity exists, entered by this actor (stage 1's
           enterer is the parent's actor), with consent_reference equal to
           the derived request reference and received_at = evaluated_at.

        The row's recorded_at is not compared. It is in practice later than
        evaluated_at, because stage 1 inserts the row after the parent's
        intake; the witnesses assert that premise, but nothing here relies on
        it, since 1-3 establish that the row is this request's own first
        stage and nothing else. A request that fails any step keeps the
        ordinary read's answer, False.

        The two imports are local, as command_intake.slots() imports the
        derivation: organism_governance_service imports this module at
        module level.
        """
        if not request.idempotency_key.startswith(_COMPOUND_ADMISSION_KEY_PREFIX):
            return False
        from src.intevia.services.command_intake import COMPOUND_ENTRIES
        from src.intevia.services.organism_governance_service import (
            _compound_stage_identifiers,
        )

        entry_values = tuple(entry.value for entry in COMPOUND_ENTRIES)
        with connections[self.database_alias].cursor() as cursor:
            cursor.execute(
                "SELECT idempotency_key"
                "  FROM public.core_commandintake"
                " WHERE actor_id = %s"
                "   AND actor_identity_id = %s"
                "   AND entry IN (" + ", ".join(["%s"] * len(entry_values)) + ")"
                "   AND action = %s"
                "   AND target_organism_id = %s"
                "   AND received_at = %s"
                " ORDER BY id",
                [
                    request.actor_pk,
                    request.actor_identity_id,
                    *entry_values,
                    OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
                    organism.organism_id,
                    request.evaluated_at,
                ],
            )
            interaction_keys = [row[0] for row in cursor.fetchall()]
        parents: list[tuple[str, str]] = []
        for interaction_key in interaction_keys:
            request_key, request_reference, admission_key, admission_reference = (
                _compound_stage_identifiers(organism.organism_id, interaction_key)
            )
            if (
                admission_key == request.idempotency_key
                and admission_reference == request.request_reference
            ):
                parents.append((request_key, request_reference))
        if len(parents) != 1:
            return False
        request_key, request_reference = parents[0]
        stage_request_uuid = uuid5(
            organism.organism_id,
            f"{OrganismAction.REQUEST_GOVERNANCE_SEAT.value}:{request_key}",
        )
        if target["request_uuid"] != str(stage_request_uuid):
            return False
        return candidates.filter(
            request_uuid=stage_request_uuid,
            enterer_identity_id=request.actor_pk,
            consent_reference=request_reference,
            received_at=request.evaluated_at,
        ).exists()

    def _qualify_governance_seat_request(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify a member's request for a Governance Circle seat
        (REQUEST_GOVERNANCE_SEAT).

        Reached from _coordinator_act once the LO folds neither DORMANT nor
        CLOSED, without the router's capacity, membership or office gates.
        evaluate_organism_authority has checked the target's key set, its
        kind, a str seat_id, canonical UUIDs and that the applicant is the
        actor, so this method's own test (a missing target or another
        applicant is E_NOT_APPLICANT) is a guard. The actor's membership in
        this LO must exist, match the request's locked heads and fold ACTIVE,
        else SBG_MEMBER_NOT_ELIGIBLE.

        Qualified facts: "kind" GOVERNANCE_SEAT_REQUEST, and "c", "r" and "s"
        the target's circle_uuid, seat_rule_uuid and seat_id. The circle,
        the seat rule and the seat are not checked here. Codes are disclosed
        by _refuse's rule. Reads only.
        """
        target = request.target
        if target is None or target.get("applicant_identity_uuid") != str(
            request.actor_identity_id
        ):
            return self._refuse(request, S015AuthorityRefusalCode.E_NOT_APPLICANT)
        membership = OrganismMembership.objects.using(self.database_alias).filter(
            identity_id=request.actor_pk,
            living_organism=organism,
        ).first()
        if (
            membership is None
            or not self._locked_heads_match(request, membership)
            or _fold_state(
                self.database_alias,
                "core_organismmembership",
                membership.pk,
                request.effective_at,
                request.evaluated_at,
            ) != "ACTIVE"
        ):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.SBG_MEMBER_NOT_ELIGIBLE,
            )
        return self._qualified(request, {
            "kind": "GOVERNANCE_SEAT_REQUEST",
            "c": target["circle_uuid"],
            "r": target["seat_rule_uuid"],
            "s": target["seat_id"],
        })

    def _qualify_governance_constitutional_seating(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify a Coordinator's appointment decision on a Governance Circle
        seat request after Birthing (ADMIT_CIRCLE_PARTICIPANT, route
        GOVERNANCE_CONSTITUTIONAL).

        Reached from _coordinator_act after Birthing, with an effective
        Coordinator office. UNRECOGNISED_ACTION when there is no target, or
        the subtype is not ADMIT_CIRCLE_PARTICIPANT, or the target's action,
        organism_uuid, kind (GOVERNANCE), route (GOVERNANCE_CONSTITUTIONAL),
        constitutional_route (APPOINTMENT) or deciding_party
        (office:LO_COORDINATOR) does not match, or its outcome is not ACCEPT
        or DECLINE, or the seat request it names is not a fact
        (_governance_request_matches_target). Then a state event taking the
        circle to ACTIVE, cut on effective_at and recorded_at, refuses
        SBG_SEAT_ROUTE_UNAVAILABLE.

        Qualified facts: "kind" GOVERNANCE, "route" GOVERNANCE_CONSTITUTIONAL,
        "constitutional_route" APPOINTMENT, and "c", "r", "s" and "q" as for
        the founding seating; the outcome is not among them. Codes are
        disclosed by _refuse's rule. Reads only.
        """
        target = request.target
        if target is None:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        if (
            request.command_subtype != OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value
            or target.get("action") != OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value
            or target.get("organism_uuid") != organism.organism_id
            or target.get("kind") != "GOVERNANCE"
            or target.get("route") != "GOVERNANCE_CONSTITUTIONAL"
            or target.get("constitutional_route") != "APPOINTMENT"
            or target.get("deciding_party") != "office:LO_COORDINATOR"
            or target.get("outcome") not in {"ACCEPT", "DECLINE"}
            or not self._governance_request_matches_target(request, organism, target)
        ):
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        if CircleStateEvent.objects.using(self.database_alias).filter(
            circle__parent_organism=organism,
            circle__circle_uuid=target["circle_uuid"],
            resulting_state="ACTIVE",
            effective_at__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        ).exists():
            return self._refuse(
                request,
                S015AuthorityRefusalCode.SBG_SEAT_ROUTE_UNAVAILABLE,
            )
        return self._qualified(request, {
            "kind": "GOVERNANCE",
            "route": "GOVERNANCE_CONSTITUTIONAL",
            "constitutional_route": "APPOINTMENT",
            "c": target["circle_uuid"],
            "r": target["seat_rule_uuid"],
            "s": target["seat_id"],
            "q": target["request_uuid"],
        })

    def _sbg_governance_readiness(
        self,
        organism_id: int,
        effective_at: datetime,
        recorded_at: datetime,
        lifecycle: str,
    ) -> S015AuthorityRefusalCode | None:
        """Return a Governance readiness code from the database, or
        None.

        Call public.sbg_governance_readiness on database_alias with
        the LO primary key, effective_at, recorded_at and lifecycle.
        Here recorded_at is the knowledge cut: all callers pass
        evaluated_at. Callers use PHASE_ADVANCE, BIRTHING or
        ENABLING.

        Migration 0045 defines the checks. First select a Governance
        Circle with a CREATE_DORMANT event recorded by the knowledge
        cut and a fold distinct from CLOSED; the fold may be NULL.
        Require an effective, recorded GOVERNANCE_CONSTITUTION rule
        with the recognised arrangement shape and at least one seat
        in effect. Missing circle/rule and invalid
        arrangement/eligibility return the corresponding SBG refusal
        code. PHASE_ADVANCE stops there.

        For ENABLING, a circle with an ELIGIBLE event at the cuts
        returns SBG_IL_GRESD if its fold is DORMANT, or succeeds if
        ELIGIBLE. Otherwise, as for BIRTHING, readiness succeeds with
        an unended participation whose member folds ACTIVE, or an
        in-effect vacant Coordinator-appointment seat with an ACTIVE
        membership and Coordinator assignment. Without either, return
        SBG_NO_INITIAL_PARTICIPATION_ROUTE. Recognising an
        arrangement's shape does not establish that every declared
        route is executable.

        Convert SQL NULL to None and a returned code to
        S015AuthorityRefusalCode. Raise RuntimeError for no row or an
        unknown code; database errors propagate. This helper adds no
        eligibility checks, acquires no command locks, writes
        nothing, and does not itself build an authority response or
        refusal.
        """
        with connections[self.database_alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.sbg_governance_readiness(%s, %s, %s, %s)",
                [organism_id, effective_at, recorded_at, lifecycle],
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("SB-G readiness function returned no row")
        refusal_code = row[0]
        if refusal_code is None:
            return None
        try:
            return S015AuthorityRefusalCode(refusal_code)
        except ValueError as exc:
            raise RuntimeError("SB-G readiness returned an unknown refusal code") from exc

    def _governance_enabling_readiness(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
        candidate: LOGovernanceConfigurationVersion,
        previous_configuration: LOGovernanceConfigurationVersion | None,
    ) -> S015AuthorityRefusalCode | None:
        """Return the Governance readiness refusal for a configuration effect
        that newly enables governance after Birthing, or None.

        Used by _qualify_gateway_change for EFFECT_GOVERNANCE_CONFIGURATION.
        It checks only when the request is that action with version_kind
        EFFECT, the candidate's governance_enabled is True, the previous
        configuration (when there is one) does not have it True, and the
        Birthing source returns a non-empty str at the request's effective_at
        and evaluated_at. Then it returns
        _sbg_governance_readiness("ENABLING"). In every other case, a draft,
        a candidate that does not newly enable governance, or any time
        before Birthing, it returns None.
        """
        if (
            request.action is not OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION
            or request.version_kind != "EFFECT"
            or candidate.governance_enabled is not True
            or (
                previous_configuration is not None
                and previous_configuration.governance_enabled is True
            )
        ):
            return None
        birthing_reference = self.birthing_source.reference_at(
            organism.organism_id,
            request.effective_at,
            request.evaluated_at,
        )
        if type(birthing_reference) is not str or not birthing_reference:
            return None
        return self._sbg_governance_readiness(
            organism.pk,
            request.effective_at,
            request.evaluated_at,
            "ENABLING",
        )

    def _qualify_governance_circle_creation(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
        """Qualify creating the LO's dormant Governance Circle
        (CREATE_DORMANT_CIRCLE).

        Reached from _coordinator_act: before Birthing by the founder, with
        no office required; after Birthing with an effective Coordinator
        office. First, any Governance Circle of this LO refuses
        SBG_SECOND_GOVERNANCE_CIRCLE. That read has no time cut and no state
        test, matching the database's one-Governance-Circle-per-LO index
        (s015_circle_one_governance_uidx, migration 0045). Then a missing
        target, or a kind other than GOVERNANCE, is UNRECOGNISED_ACTION (a
        guard: evaluate_organism_authority has checked both).

        Qualified facts: "kind" GOVERNANCE and "c" the target's circle_uuid.
        Codes are disclosed by _refuse's rule. Reads only.
        """
        if Circle.objects.using(self.database_alias).filter(
            parent_organism=organism,
            kind="GOVERNANCE",
        ).exists():
            return self._refuse(
                request,
                S015AuthorityRefusalCode.SBG_SECOND_GOVERNANCE_CIRCLE,
            )
        target = request.target
        if target is None or target.get("kind") != "GOVERNANCE":
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        return self._qualified(request, {
            "kind": "GOVERNANCE",
            "c": target["circle_uuid"],
        })

    def _lo_capacity(self, organism: LivingOrganism, request: S015AuthorityRequest) -> bool:
        """Return whether the LO has capacity for an act at the request's
        time cuts.

        False unless the LO has its sequence-1 event (its founding)
        effective by request.effective_at and recorded by
        request.evaluated_at. That read has no received_at cut and does not
        test the event's action. True when the LO folds anything but CLOSED
        (_lo_state), including DORMANT and None. When it folds CLOSED, fold
        again one microsecond before effective_at at the same evaluated_at:
        an LO closed at exactly the act's effective_at keeps capacity for
        that act; one closed earlier has none.

        Every caller refuses False with C_LO_NO_CAPACITY. Reads only,
        without locks.
        """
        founding = LivingOrganismEvent.objects.using(self.database_alias).filter(
            living_organism=organism,
            sequence=1,
            effective_at__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        ).exists()
        if not founding:
            return False
        if self._lo_state(organism, request) != "CLOSED":
            return True
        # A closure at the act's exact effective time retains capacity for that coordinate.
        state_before_act = _fold_state(
            self.database_alias,
            "core_livingorganism",
            organism.pk,
            request.effective_at - timedelta(microseconds=1),
            request.evaluated_at,
        )
        return state_before_act != "CLOSED"

    def _locked_heads_match(
        self,
        request: S015PostFoundingAuthorityRequest,
        membership: OrganismMembership,
    ) -> bool:
        """Compare supplied locked-head references with current heads.

        Any locking is the caller's. This method neither acquires
        locks nor verifies that the caller holds them.

        Return True only when:
        - the membership has a head transition whose lineage
          reference equals request.active_membership_head_reference;
        - its latest transition at request.effective_at and
          evaluated_at exists and is that current head or one of its
          predecessors;
        - the head lineage references of this membership's
          assignments with cached state ACTIVE and a non-null head,
          ordered by assignment primary key, equal the supplied
          contextual_role_head_references tuple exactly;
        - each of those assignments has a latest event at the same
          cuts that is its current head or one of the head's
          predecessors.

        Latest transitions use _latest_transition; ancestry uses
        _is_ancestor. The assignment set comes from the current
        cached state, not a fold at the request's cuts. An empty
        assignment set can match an empty tuple; this does not prove
        office eligibility.

        A failed comparison returns False; database errors propagate.
        Callers translate False to DENIED, except _coordinator_act
        uses E_NOT_EFFECTIVE_COORDINATOR and the seat-request
        qualifier uses SBG_MEMBER_NOT_ELIGIBLE. Reads only.
        """
        if membership.head_transition_id is None or membership.head_transition.lineage_reference != request.active_membership_head_reference:
            return False
        effective_membership_head = _latest_transition(
            self.database_alias,
            OrganismMembershipTransition,
            "membership_id",
            membership.pk,
            request.effective_at,
            request.evaluated_at,
        )
        if effective_membership_head is None or not self._is_ancestor(
            membership.head_transition, effective_membership_head,
        ):
            return False
        locked_refs = tuple(
            ContextualRoleAssignment.objects.using(self.database_alias).filter(
                membership=membership,
                state="ACTIVE",
                head_state_event__isnull=False,
            ).order_by("pk").values_list("head_state_event__lineage_reference", flat=True)
        )
        if locked_refs != request.contextual_role_head_references:
            return False
        for reference in locked_refs:
            assignment = ContextualRoleAssignment.objects.using(self.database_alias).select_related(
                "head_state_event",
            ).get(head_state_event__lineage_reference=reference)
            effective_office_head = _latest_transition(
                self.database_alias,
                ContextualRoleAssignmentStateEvent,
                "assignment_id",
                assignment.pk,
                request.effective_at,
                request.evaluated_at,
            )
            if effective_office_head is None or not self._is_ancestor(
                assignment.head_state_event, effective_office_head,
            ):
                return False
        return True

    @staticmethod
    def _is_ancestor(head, possible_predecessor) -> bool:
        """Return whether possible_predecessor is head itself or on head's
        predecessor chain.

        Walk head, head.predecessor and so on, comparing primary keys, until
        a match (True) or a row with no predecessor (False). Each step may
        read the predecessor row from the database. There is no guard
        against a cycle in the chain.
        """
        current = head
        while current is not None:
            if current.pk == possible_predecessor.pk:
                return True
            current = current.predecessor
        return False

    def _effective_coordinator_office(
        self,
        request: S015PostFoundingAuthorityRequest,
        membership: OrganismMembership,
    ) -> ContextualRoleAssignment | None:
        """Return the membership's effective Coordinator office, or None.

        Consider the membership's assignments of an active role definition
        with code COORDINATOR and scope LIVING_ORGANISM, in primary-key
        order. Return the first that:
        - has a head state event whose lineage_reference is among
          request.contextual_role_head_references;
        - folds ACTIVE at request.effective_at and request.evaluated_at
          (_fold_state);
        - has a latest state event at those cuts (_latest_transition);
        - has its term in effect (_assignment_term_in_effect).

        None when no assignment qualifies. Callers refuse None with
        E_NOT_EFFECTIVE_COORDINATOR. Reads only, without locks.
        """
        assignments = ContextualRoleAssignment.objects.using(self.database_alias).filter(
            membership=membership,
            role_definition__code="COORDINATOR",
            role_definition__scope="LIVING_ORGANISM",
            role_definition__active=True,
        ).select_related("role_definition", "head_state_event")
        for assignment in assignments.order_by("pk"):
            if assignment.head_state_event_id is None:
                continue
            if assignment.head_state_event.lineage_reference not in request.contextual_role_head_references:
                continue
            if _fold_state(
                self.database_alias, "core_contextualroleassignment", assignment.pk,
                request.effective_at, request.evaluated_at,
            ) != "ACTIVE":
                continue
            term = _latest_transition(
                self.database_alias,
                ContextualRoleAssignmentStateEvent,
                "assignment_id",
                assignment.pk,
                request.effective_at,
                request.evaluated_at,
            )
            if term is None:
                continue
            if not _assignment_term_in_effect(
                self.database_alias, assignment.pk, request.effective_at, request.evaluated_at,
            ):
                continue
            return assignment
        return None

    def _eligible_coordinator_count(self, organism: LivingOrganism, request: S015AuthorityRequest) -> int:
        """Count the LO's members who hold an effective Coordinator office.

        For every membership row of the LO that folds ACTIVE at
        request.effective_at and request.evaluated_at, count it once if any
        of its assignments of an active COORDINATOR role definition with
        scope LIVING_ORGANISM folds ACTIVE at those cuts and has its term in
        effect (_term_in_effect). PROBATIONARY members are not counted. Unlike
        _effective_coordinator_office, no head state event or locked head
        reference is required.

        Used by the role proposal and the role transition's ASSIGN (zero
        refuses C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED) and by application
        processing (with CQ active, any count but one refuses
        C_ROUTE_IS_CQ, except for a decision). Reads only, without locks.
        """
        count = 0
        memberships = OrganismMembership.objects.using(self.database_alias).filter(living_organism=organism)
        for membership in memberships:
            if _fold_state(
                self.database_alias, "core_organismmembership", membership.pk,
                request.effective_at, request.evaluated_at,
            ) == "ACTIVE":
                assignments = ContextualRoleAssignment.objects.using(self.database_alias).filter(
                    membership=membership,
                    role_definition__code="COORDINATOR",
                    role_definition__scope="LIVING_ORGANISM",
                    role_definition__active=True,
                )
                if any(
                    _fold_state(
                        self.database_alias, "core_contextualroleassignment", assignment.pk,
                        request.effective_at, request.evaluated_at,
                    ) == "ACTIVE"
                    and self._term_in_effect(assignment, request)
                    for assignment in assignments
                ):
                    count += 1
        return count

    def _term_in_effect(self, assignment: ContextualRoleAssignment, request: S015AuthorityRequest) -> bool:
        """Return _assignment_term_in_effect for the assignment at the
        request's effective_at and evaluated_at. Used by
        _eligible_coordinator_count."""
        return _assignment_term_in_effect(
            self.database_alias,
            assignment.pk,
            request.effective_at,
            request.evaluated_at,
        )

    def _qualify_application_processing(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
        membership: OrganismMembership,
        office: ContextualRoleAssignment,
    ) -> S015AuthorityResult:
        """Qualify claiming, reassigning or deciding an LO application.

        The router supplies an ACTIVE LO, an ACTIVE actor membership
        with matching supplied heads, and an effective Coordinator
        office. The dispatcher checks application_uuid has type UUID;
        the same local check refuses UNRECOGNISED_ACTION defensively.

        In order:
        - require this LO's application, effective by effective_at
          and recorded by evaluated_at, else DENIED;
        - refuse C_TERMINAL_ROW_STANDS for a decision or withdrawal
          recorded by evaluated_at, without an effective-time test;
        - select the eligible engagement with highest ordinal. LO_CQ,
          CIRCLE_CQ and CIRCLE_CSA refuse C_ROUTE_IS_CQ; another
          route except LO_CSA refuses UNRECOGNISED_ROUTE;
        - for DECIDE_APPLICATION require an LO_CSA engagement, else
          UNRECOGNISED_ROUTE, and resolve its recorded settings basis
          through _settings_basis_versions. Otherwise resolve
          configuration and settings at the request's cuts. A missing
          version is C_CONFIGURATION_NOT_ESTABLISHED;
        - except for a decision, require processing_responsibility LO
          and, when CQ is True, exactly one eligible Coordinator
          member. Either failure is C_ROUTE_IS_CQ.

        Select the latest CSA claim by effective_at, recorded_at and
        PK, all descending. CLAIM_APPLICATION refuses DENIED if an
        engagement already exists. REASSIGN_APPLICATION requires an
        engagement and claim with no eligible end, else DENIED; it
        does not require the actor to be that claim's assignee.

        DECIDE_APPLICATION requires assignment_uuid to name the
        claim, else E_NOT_ASSIGNEE; the engagement reference to name
        that claim, else UNRECOGNISED_ROUTE; the claim's actor and
        assignee membership to match this actor and membership, else
        E_NOT_ASSIGNEE; the assignee office to match this office,
        else E_NOT_EFFECTIVE_COORDINATOR; and no eligible end, else
        E_NOT_ASSIGNEE. An unsupported action is UNRECOGNISED_ACTION.

        Application, engagement, claim and end reads use effective
        and recorded cuts, without separate received_at predicates.
        Terminal reads use recorded time alone. Version selection has
        its own receipt and recorded predicates through
        _effective_version. For decisions its cuts are the
        engagement's effective_at and recorded_at; other version
        selection uses the request's cuts.

        Qualified facts extend _coordinator_facts: claims add "k": C
        and "t": CQ_INACTIVE or SOLE_COORDINATOR; reassignments and
        decisions add "a": the claim UUID and "e": the engagement
        ordinal. Refusals use _refuse's disclosure rule. This method
        reads without command locks and writes no claim, engagement,
        end or decision.
        """
        application_uuid = request.target.get("application_uuid")
        if type(application_uuid) is not UUID:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        application = Application.objects.using(self.database_alias).filter(
            application_uuid=application_uuid,
            living_organism=organism,
            effective_at__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        ).first()
        if application is None:
            return self._refuse(request, S015AuthorityRefusalCode.DENIED)
        if ApplicationDecision.objects.using(self.database_alias).filter(
            application=application,
            recorded_at__lte=request.evaluated_at,
        ).exists() or ApplicationWithdrawal.objects.using(self.database_alias).filter(
            application=application,
            recorded_at__lte=request.evaluated_at,
        ).exists():
            return self._refuse(request, S015AuthorityRefusalCode.C_TERMINAL_ROW_STANDS)
        engagements = ApplicationEngagement.objects.using(self.database_alias).filter(
            application=application,
            effective_at__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        ).order_by("-engagement_ordinal")
        engagement = engagements.first()
        if engagement is not None and engagement.route in {"LO_CQ", "CIRCLE_CQ", "CIRCLE_CSA"}:
            return self._refuse(request, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
        if engagement is not None and engagement.route != "LO_CSA":
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ROUTE)
        if request.action is OrganismAction.DECIDE_APPLICATION:
            if engagement is None or engagement.route != "LO_CSA":
                return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ROUTE)
            configuration, settings = self._settings_basis_versions(engagement, organism)
        else:
            settings = _effective_version(
                self.database_alias, organism, OrganismAction.EFFECT_APPLICATION_SETTINGS,
                request.effective_at, request.evaluated_at,
            )
            configuration = _effective_version(
                self.database_alias, organism, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
                request.effective_at, request.evaluated_at,
            )
        if settings is None or configuration is None:
            return self._refuse(request, S015AuthorityRefusalCode.C_CONFIGURATION_NOT_ESTABLISHED)
        if request.action is not OrganismAction.DECIDE_APPLICATION:
            if settings.processing_responsibility != "LO":
                return self._refuse(request, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
            if configuration.cq_active is True and self._eligible_coordinator_count(organism, request) != 1:
                return self._refuse(request, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
        claim = CSAAssignment.objects.using(self.database_alias).filter(
            application=application,
            effective_at__lte=request.effective_at,
            recorded_at__lte=request.evaluated_at,
        ).order_by("-effective_at", "-recorded_at", "-pk").first()
        if request.action is OrganismAction.CLAIM_APPLICATION:
            if engagement is not None:
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
            facts = self._coordinator_facts(request, membership, office, configuration, settings)
            facts["k"] = "C"
            facts["t"] = "CQ_INACTIVE" if configuration.cq_active is not True else "SOLE_COORDINATOR"
            return self._qualified(request, facts)
        if request.action is OrganismAction.REASSIGN_APPLICATION:
            if engagement is None or claim is None:
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
            current_end = CSAAssignmentEnd.objects.using(self.database_alias).filter(
                assignment=claim,
                effective_at__lte=request.effective_at,
                recorded_at__lte=request.evaluated_at,
            ).exists()
            if current_end:
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
            facts = self._coordinator_facts(request, membership, office, configuration, settings)
            facts.update({
                "a": str(claim.assignment_uuid),
                "e": engagement.engagement_ordinal,
            })
            return self._qualified(request, facts)
        if request.action is OrganismAction.DECIDE_APPLICATION:
            if request.assignment_uuid is None or claim is None or claim.assignment_uuid != request.assignment_uuid:
                return self._refuse(request, S015AuthorityRefusalCode.E_NOT_ASSIGNEE)
            if engagement is None or engagement.route != "LO_CSA" or engagement.engagement_reference != str(claim.assignment_uuid):
                return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ROUTE)
            if claim.actor_id != request.actor_pk or claim.assignee_membership_id != membership.pk:
                return self._refuse(request, S015AuthorityRefusalCode.E_NOT_ASSIGNEE)
            if claim.assignee_office_id != office.pk:
                return self._refuse(request, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
            if CSAAssignmentEnd.objects.using(self.database_alias).filter(
                assignment=claim,
                effective_at__lte=request.effective_at,
                recorded_at__lte=request.evaluated_at,
            ).exists():
                return self._refuse(request, S015AuthorityRefusalCode.E_NOT_ASSIGNEE)
            facts = self._coordinator_facts(request, membership, office, configuration, settings)
            facts.update({
                "a": str(claim.assignment_uuid),
                "e": engagement.engagement_ordinal,
            })
            return self._qualified(request, facts)
        return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)

    def _qualify_gateway_change(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
        membership: OrganismMembership,
        office: ContextualRoleAssignment,
    ) -> S015AuthorityResult:
        """Qualify a gateway change: application settings, the entitlement
        election by version, the application question or the governance
        configuration (the _VERSION_SPECS actions).

        Reached from _coordinator_act after its capacity and LO-state gates
        (which also admit a non-ACTIVE LO for the founder's first
        configuration, and a FOUNDING_PENDING LO for a gateway change once a
        configuration is effective), with the actor's locked, ACTIVE
        membership and an effective Coordinator office. Read the effective
        configuration and, except for a configuration change, the effective
        settings at request.effective_at and request.evaluated_at.

        The route:
        - with no effective configuration, only a configuration change by
          the LO's founder proceeds: another action refuses
          C_CONFIGURATION_NOT_ESTABLISHED, another actor
          E_NOT_EFFECTIVE_COORDINATOR. The basis is FIRST_CONFIGURATION;
        - otherwise inherit_portal_cq_settings True refuses C_ROUTE_IS_CQ.
          For a configuration change naming a version, a candidate recorded
          by evaluated_at that would set cq_active or either require-CQ flag
          to False, while CQ is active and changing LO settings requires CQ,
          refuses C_CHANGE_SELF_AUTHORISING. Then, with CQ active and the
          action's require-CQ flag True (the application question's flag for
          a question, the LO settings flag otherwise), refuse
          SE_OPT_IN_NEEDS_CQ for an election and C_ROUTE_IS_CQ for the rest.
          The basis is CQ_INACTIVE, or CHANGE_FLAG_NO when CQ is active.

        When request.version_uuid is set, the candidate is the action's
        version row of this LO with that UUID, recorded by evaluated_at
        (no recorded_at cut for an election), with entitlement choice and
        route null for settings and non-null for an election. Then, in order:
        - no candidate refuses DENIED;
        - an event of this action effective by effective_at and received
          and recorded by evaluated_at that already references the version
          refuses DENIED;
        - a version_kind other than DRAFT or EFFECT is UNRECOGNISED_ACTION
          (a guard);
        - an EFFECT whose version is incomplete (_version_complete) refuses
          DENIED;
        - for a configuration, any Governance enabling readiness refusal
          (_governance_enabling_readiness);
        - for a configuration, its nomination policy (None or empty means
          C) other than A, B or C refuses DENIED. Policy A or B needs a
          non-empty str from the nomination entitlement source, else
          E_NOMINATION_POLICY_NOT_ENTITLED with _coordinator_facts less "s",
          "i": None and "v": the candidate UUID. The policy checks run for a
          DRAFT too.

        When version_uuid is None, none of these version checks run.
        Qualified facts are _coordinator_facts ("s" removed for a
        configuration), "t": the basis, "v": the version UUID or "-", and
        "i": the entitlement reference when one was required. Refusals use
        _refuse's disclosure rule. Reads only, without locks.
        """
        configuration = _effective_version(
            self.database_alias, organism, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            request.effective_at, request.evaluated_at,
        )
        settings = None if request.action is OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION else _effective_version(
            self.database_alias, organism, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            request.effective_at, request.evaluated_at,
        )
        if configuration is None:
            if request.action is not OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION:
                return self._refuse(request, S015AuthorityRefusalCode.C_CONFIGURATION_NOT_ESTABLISHED)
            if organism.founder_identity_id != request.actor_pk:
                return self._refuse(request, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)
            route_basis = "FIRST_CONFIGURATION"
        else:
            if configuration.inherit_portal_cq_settings is True:
                return self._refuse(request, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
            if request.action is OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION and request.version_uuid is not None:
                candidate = LOGovernanceConfigurationVersion.objects.using(self.database_alias).filter(
                    version_uuid=request.version_uuid,
                    living_organism=organism,
                    recorded_at__lte=request.evaluated_at,
                ).first()
                if (
                    candidate is not None
                    and configuration.cq_active is True
                    and configuration.require_cq_to_change_lo_settings is True
                    and (
                        candidate.cq_active is False
                        or candidate.require_cq_to_change_lo_settings is False
                        or candidate.require_cq_to_change_application_question is False
                    )
                ):
                    return self._refuse(request, S015AuthorityRefusalCode.C_CHANGE_SELF_AUTHORISING)
            cq_required = (
                configuration.require_cq_to_change_application_question
                if request.action is OrganismAction.EFFECT_APPLICATION_QUESTION
                else configuration.require_cq_to_change_lo_settings
            )
            if configuration.cq_active is True and cq_required is True:
                refusal_code = (
                    S015AuthorityRefusalCode.SE_OPT_IN_NEEDS_CQ
                    if request.action is OrganismAction.EFFECT_ENTITLEMENT_ELECTION
                    else S015AuthorityRefusalCode.C_ROUTE_IS_CQ
                )
                return self._refuse(request, refusal_code)
            route_basis = "CQ_INACTIVE" if configuration.cq_active is not True else "CHANGE_FLAG_NO"
        nomination_entitlement_reference = None
        if request.version_uuid is not None:
            model, prefix = _VERSION_SPECS[request.action]
            candidates = model.objects.using(self.database_alias).filter(
                version_uuid=request.version_uuid,
                living_organism=organism,
            )
            if request.action is not OrganismAction.EFFECT_ENTITLEMENT_ELECTION:
                candidates = candidates.filter(
                    recorded_at__lte=request.evaluated_at,
                )
            # K4-a: the election is bound by request identity; authority remains at received_at.
            if request.action is OrganismAction.EFFECT_APPLICATION_SETTINGS:
                candidates = candidates.filter(
                    entitlement_choice__isnull=True,
                    entitlement_election_route__isnull=True,
                )
            elif request.action is OrganismAction.EFFECT_ENTITLEMENT_ELECTION:
                candidates = candidates.filter(
                    entitlement_choice__isnull=False,
                    entitlement_election_route__isnull=False,
                )
            candidate = candidates.first()
            if candidate is None:
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
            events = LivingOrganismEvent.objects.using(self.database_alias).filter(
                living_organism=organism,
                action=request.action.value,
                effective_at__lte=request.effective_at,
                received_at__lte=request.evaluated_at,
                recorded_at__lte=request.evaluated_at,
            )
            current = events.order_by(
                "-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence", "-event_uuid",
            ).values_list("evidence_reference", flat=True).first()
            reference = prefix + str(candidate.version_uuid)
            if current == reference or events.filter(evidence_reference=reference).exists():
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
            if request.version_kind not in {"DRAFT", "EFFECT"}:
                return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
            if request.version_kind == "EFFECT" and not self._version_complete(
                candidate,
                request.action,
            ):
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
            if request.action is OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION:
                readiness_refusal = self._governance_enabling_readiness(
                    request,
                    organism,
                    candidate,
                    configuration,
                )
                if readiness_refusal is not None:
                    return self._refuse(request, readiness_refusal)
            if request.action is OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION:
                policy = candidate.coordinator_nomination_policy or "C"
                if policy not in {"A", "B", "C"}:
                    return self._refuse(request, S015AuthorityRefusalCode.DENIED)
                if policy in {"A", "B"}:
                    reference = self.nomination_entitlement_source.reference_at(
                        organism.organism_id, request.effective_at, request.evaluated_at,
                    )
                    if type(reference) is not str or not reference:
                        refusal_facts = self._coordinator_facts(
                            request, membership, office, configuration, settings,
                        )
                        refusal_facts.pop("s", None)
                        refusal_facts.update({"i": None, "v": str(candidate.version_uuid)})
                        return self._refuse(
                            request,
                            S015AuthorityRefusalCode.E_NOMINATION_POLICY_NOT_ENTITLED,
                            facts=refusal_facts,
                        )
                    nomination_entitlement_reference = reference
        facts = self._coordinator_facts(request, membership, office, configuration, settings)
        if request.action is OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION:
            facts.pop("s", None)
        facts.update({
            "t": route_basis,
            "v": "-" if request.version_uuid is None else str(request.version_uuid),
        })
        if nomination_entitlement_reference is not None:
            facts["i"] = nomination_entitlement_reference
        return self._qualified(request, facts)

    def _coordinator_facts(
        self,
        request: S015PostFoundingAuthorityRequest,
        membership: OrganismMembership,
        office: ContextualRoleAssignment,
        configuration,
        settings,
    ) -> dict[str, object]:
        """Return the common facts of a Coordinator's act.

        "c": the configuration's version UUID; "m": the event UUID of the
        membership's latest transition; "o": the event UUID of the office's
        latest state event; "s": the settings' version UUID. Each is "-"
        when absent. The transitions are read with _latest_transition at
        request.effective_at and request.evaluated_at. Callers add or remove
        keys for their action. Reads only, without locks.
        """
        membership_head = _latest_transition(
            self.database_alias,
            OrganismMembershipTransition,
            "membership_id",
            membership.pk,
            request.effective_at,
            request.evaluated_at,
        )
        office_head = _latest_transition(
            self.database_alias,
            ContextualRoleAssignmentStateEvent,
            "assignment_id",
            office.pk,
            request.effective_at,
            request.evaluated_at,
        )
        return {
            "c": "-" if configuration is None else str(configuration.version_uuid),
            "m": "-" if membership_head is None else str(membership_head.event_uuid),
            "o": "-" if office_head is None else str(office_head.event_uuid),
            "s": "-" if settings is None else str(settings.version_uuid),
        }

    def _settings_basis_versions(
        self,
        engagement: ApplicationEngagement,
        organism: LivingOrganism,
    ) -> tuple[LOGovernanceConfigurationVersion | None, LOApplicationSettingsVersion | None]:
        """Return the configuration and settings versions an engagement
        recorded as its basis, or (None, None).

        Return (None, None) when the engagement has no recorded_at, or its
        settings_basis does not parse as "key:value" parts joined by ";"
        with no repeated key, or lacks a governance-configuration-version or
        application-settings-version value that parses as a UUID. Other keys
        are ignored. Load that configuration version and that settings
        version of this LO, the settings with null entitlement fields; both
        must exist. They must also be the effective configuration and
        settings (_effective_version) at the engagement's own effective_at
        and recorded_at, else (None, None).

        Used for DECIDE_APPLICATION, so a decision stands on the basis
        recorded at engagement rather than the versions in effect at the
        request. Reads only, without locks.
        """
        if engagement.recorded_at is None:
            return None, None
        references = {}
        for part in engagement.settings_basis.split(";"):
            key, separator, value = part.partition(":")
            if not separator or key in references:
                return None, None
            references[key] = value
        try:
            configuration_uuid = UUID(references["governance-configuration-version"])
            settings_uuid = UUID(references["application-settings-version"])
        except (KeyError, ValueError):
            return None, None
        configuration = LOGovernanceConfigurationVersion.objects.using(self.database_alias).filter(
            version_uuid=configuration_uuid,
            living_organism=organism,
        ).first()
        settings = LOApplicationSettingsVersion.objects.using(self.database_alias).filter(
            version_uuid=settings_uuid,
            living_organism=organism,
            entitlement_choice__isnull=True,
            entitlement_election_route__isnull=True,
        ).first()
        if configuration is None or settings is None:
            return None, None
        effective_configuration = _effective_version(
            self.database_alias, organism, OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            engagement.effective_at, engagement.recorded_at,
        )
        effective_settings = _effective_version(
            self.database_alias, organism, OrganismAction.EFFECT_APPLICATION_SETTINGS,
            engagement.effective_at, engagement.recorded_at,
        )
        if (
            effective_configuration is None
            or effective_settings is None
            or effective_configuration.version_uuid != configuration_uuid
            or effective_settings.version_uuid != settings_uuid
        ):
            return None, None
        return configuration, settings

    @staticmethod
    def _version_complete(version, action: OrganismAction) -> bool:
        """Return whether a gateway version is complete enough to take
        effect for the action.

        Application settings rows:
        - for EFFECT_ENTITLEMENT_ELECTION: choice COMPLIMENTARY_BETA, route
          POST_BIRTHING, a policy version above 0, a non-empty policy text
          digest, a non-blank authority source and version, and
          applications_open, automatic_acceptance and
          processing_responsibility all None;
        - for EFFECT_APPLICATION_SETTINGS: every entitlement field None,
          applications_open and automatic_acceptance set, and
          processing_responsibility LO or APPLICATION_CIRCLE;
        - for any other action, False.

        An application question: non-blank question_text and a set
        answer_format. Any other row (a governance configuration):
        cq_active, inherit_portal_cq_settings and both require-CQ flags set.
        The action is not checked for those two, and no other configuration
        field is required.
        """
        if isinstance(version, LOApplicationSettingsVersion):
            election_fields = (
                version.entitlement_choice,
                version.entitlement_election_route,
                version.entitlement_policy_version,
                version.entitlement_policy_text_sha256,
                version.entitlement_authority_source,
                version.entitlement_authority_version,
            )
            if action is OrganismAction.EFFECT_ENTITLEMENT_ELECTION:
                return (
                    version.entitlement_choice == "COMPLIMENTARY_BETA"
                    and version.entitlement_election_route == "POST_BIRTHING"
                    and version.entitlement_policy_version is not None
                    and version.entitlement_policy_version > 0
                    and bool(version.entitlement_policy_text_sha256)
                    and version.entitlement_authority_source is not None
                    and bool(version.entitlement_authority_source.strip())
                    and version.entitlement_authority_version is not None
                    and bool(version.entitlement_authority_version.strip())
                    and version.applications_open is None
                    and version.automatic_acceptance is None
                    and version.processing_responsibility is None
                )
            if action is not OrganismAction.EFFECT_APPLICATION_SETTINGS:
                return False
            return (
                all(field is None for field in election_fields)
                and version.applications_open is not None
                and version.automatic_acceptance is not None
                and version.processing_responsibility in {"LO", "APPLICATION_CIRCLE"}
            )
        if isinstance(version, LOApplicationQuestionVersion):
            return bool(version.question_text.strip()) and version.answer_format is not None
        return all(getattr(version, field) is not None for field in (
            "cq_active",
            "inherit_portal_cq_settings",
            "require_cq_to_change_lo_settings",
            "require_cq_to_change_application_question",
        ))

    @staticmethod
    def _encode_facts(
        request: S015AuthorityRequest,
        facts: dict[str, object],
        *,
        refusal_code: str | None,
    ) -> str:
        """Validate facts against the declared vocabulary and encode them.

        refusal_code None means a qualified output: it must match the
        action's one entry for the request's form by its discriminators,
        carry every required key and no undeclared key, no x, and lawful
        values. A code means a refusal output: its x must equal the code
        and its other keys must be declared by the form's refusal shape
        (core.organism_authority_facts.validate_authority_facts). A defect
        raises S015AuthorityFactsUndeclared, a ValueError, inside the
        provider; S015Authority.qualify reports it as
        S015AuthorityUnavailable with the cause attached and writes no
        decision row. Returns "s015a1:" plus the canonical JSON of facts;
        it never checks the length, which each caller does.
        """
        validate_authority_facts(request, facts, refusal_code=refusal_code)
        return S015_AUTHORITY_REFERENCE_PREFIX + canonical_json_bytes(facts).decode("utf-8")

    @staticmethod
    def _qualified(request: S015AuthorityRequest, facts: dict[str, object]) -> S015AuthorityResult:
        """Build the qualified response carrying facts as its structured
        reference, or the size refusal when the encoded reference exceeds
        the 255-character cap. Undeclared facts raise
        S015AuthorityFactsUndeclared from _encode_facts before any size
        check. Returns the own-applicant or post-founding response carrier
        by the request type.
        """
        authority_reference = ProductionApplicationAuthorityProvider._encode_facts(
            request, facts, refusal_code=None,
        )
        if len(authority_reference) > S015_AUTHORITY_REFERENCE_MAX_LENGTH:
            return ProductionApplicationAuthorityProvider._refusal_for_size(request)
        fields = asdict(request)
        fields["authority_reference"] = authority_reference
        if type(request) is S015OwnApplicantAuthorityRequest:
            return S015OwnApplicantAuthorityResponse(**fields)
        return S015PostFoundingAuthorityResponse(**fields)

    @staticmethod
    def _refusal_for_size(request: S015AuthorityRequest) -> S015AuthorityResult:
        """Build the C_REFERENCE_UNREPRESENTABLE refusal, disclosed as
        DENIED, for a lawful qualified output whose reference would exceed
        the cap. Its reference is {"x": "C_REFERENCE_UNREPRESENTABLE"},
        42 characters, encoded without a size check so it cannot recurse.
        Returns the pre-founding, own-applicant or post-founding refusal
        carrier by the request type.
        """
        code = S015AuthorityRefusalCode.C_REFERENCE_UNREPRESENTABLE
        fields = asdict(request)
        fields["refusal_code"] = code
        fields["disclosed_refusal_code"] = S015AuthorityRefusalCode.DENIED
        fields["authority_reference"] = ProductionApplicationAuthorityProvider._encode_facts(
            request, {S015_AUTHORITY_REFUSAL_KEY: code.value}, refusal_code=code.value,
        )
        if type(request) is S015PreFoundingAuthorityRequest:
            return S015PreFoundingAuthorityRefusal(**fields)
        if type(request) is S015OwnApplicantAuthorityRequest:
            return S015OwnApplicantAuthorityRefusal(**fields)
        return S015PostFoundingAuthorityRefusal(**fields)


__all__ = [
    "BirthingFactSource",
    "GovernedRegistrationFactSource",
    "ProductionApplicationAuthorityProvider",
    "ProductionBirthingFactSource",
    "ProductionGovernedRegistrationFactSource",
]