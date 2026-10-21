"""Production authority qualification for the Round 1 application routes."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from django.db import connections

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


class BirthingFactSource(Protocol):
    def reference_at(
        self, living_organism_id: UUID, effective_at: datetime, evaluated_at: datetime,
    ) -> str | None: ...


class GovernedRegistrationFactSource(Protocol):
    def reference_at(
        self, identity_id: UUID, effective_at: datetime, evaluated_at: datetime,
    ) -> str | None: ...


class CoordinatorNominationEntitlementFactSource(Protocol):
    def reference_at(
        self, living_organism_id: UUID, effective_at: datetime, evaluated_at: datetime,
    ) -> str | None: ...


def fixture_terms_permitted(database_alias: str) -> bool:
    database_name = connections[database_alias].settings_dict.get("NAME")
    return (
        type(database_name) is str
        and database_name.startswith("test_intevia_living_organism_")
        and database_name != "test_intevia_living_organism_"
    )


class ProductionBirthingFactSource:
    """Read the eligible recorded Birthing event at the supplied coordinates."""

    def __init__(self, *, database_alias: str = "default") -> None:
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.database_alias = database_alias

    def reference_at(
        self,
        living_organism_id: UUID,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> str | None:
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
    """S3-IL-REG: only a registration record proves the governed origin event."""

    def reference_at(
        self,
        identity_id: UUID,
        effective_at: datetime,
        evaluated_at: datetime,
    ) -> str | None:
        registration = LearnerRegistration.objects.filter(
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
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.database_alias = database_alias
        self.birthing_source = birthing_source or ProductionBirthingFactSource(database_alias=database_alias)
        self.registration_source = registration_source or ProductionGovernedRegistrationFactSource()
        self.nomination_entitlement_source = (
            nomination_entitlement_source or ProductionCoordinatorNominationEntitlementFactSource()
        )

    def evaluate_organism_authority(self, request: S015AuthorityRequest) -> S015AuthorityResult:
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
                    "constitutional_route", "request_uuid", "applicant_identity_uuid",
                    "deciding_party", "outcome",
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
        try:
            registration_uuid = UUID(registration_reference.removeprefix("learner-registration:"))
        except (TypeError, ValueError):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_NOT_REGISTERED,
                S015AuthorityRefusalCode.E_NOT_REGISTERED,
            )
        if registration_reference != f"learner-registration:{registration_uuid}":
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_NOT_REGISTERED,
                S015AuthorityRefusalCode.E_NOT_REGISTERED,
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
        route = "OFFICE_HOLDER" if holder_act else "EFFECTIVE_COORDINATOR"
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
                OrganismAction.REQUEST_GOVERNANCE_SEAT,
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
        return CircleJoinRequest.objects.using(self.database_alias).filter(
            request_uuid=target["request_uuid"],
            circle__parent_organism=organism,
            circle__circle_uuid=target["circle_uuid"],
            membership__identity__identity_id=target["applicant_identity_uuid"],
            seat_rule_uuid=target["seat_rule_uuid"],
            seat_id=target["seat_id"],
            initiated_by="APPLICANT",
            effective_at__lte=request.effective_at,
            received_at__lte=request.evaluated_at,
            recorded_at__lte=request.evaluated_at,
        ).exists()

    def _qualify_governance_seat_request(
        self,
        request: S015PostFoundingAuthorityRequest,
        organism: LivingOrganism,
    ) -> S015AuthorityResult:
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
    def _row_uuid(reference: str) -> str:
        try:
            return str(UUID(reference))
        except (TypeError, ValueError):
            return reference

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