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
    CSAAssignment,
    CSAAssignmentEnd,
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    LivingOrganism,
    LivingOrganismEvent,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    LOGovernanceConfigurationVersion,
    OrganismMembership,
    OrganismMembershipTransition,
    OrganismRoleDefinition,
    OfficeAppointmentConsent,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    S015AuthorityRefusalCode,
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
)


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


class ProductionBirthingFactSource:
    """S3-IL-BIRTH: no governed Birthing representation is installed in this release."""

    def reference_at(self, _living_organism_id: UUID, _effective_at: datetime, _evaluated_at: datetime) -> None:
        return None


class ProductionGovernedRegistrationFactSource:
    """S3-IL-REG: identity rows and PROVISION transitions are not governed registration."""

    def reference_at(self, _identity_id: UUID, _effective_at: datetime, _evaluated_at: datetime) -> None:
        return None


class ProductionCoordinatorNominationEntitlementFactSource:
    """No subscription-entitlement source is installed in this release."""

    def reference_at(self, _living_organism_id: UUID, _effective_at: datetime, _evaluated_at: datetime) -> None:
        return None


_VERSION_SPECS = {
    OrganismAction.EFFECT_APPLICATION_SETTINGS: (
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
    OrganismAction.EFFECT_APPLICATION_QUESTION,
    OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
    OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
    OrganismAction.RECORD_OFFICE_CONSENT,
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
    return model.objects.using(database_alias).filter(
        version_uuid=version_uuid,
        living_organism=organism,
    ).first()


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
    provider_version = "R2.0"

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
        self.birthing_source = birthing_source or ProductionBirthingFactSource()
        self.registration_source = registration_source or ProductionGovernedRegistrationFactSource()
        self.nomination_entitlement_source = (
            nomination_entitlement_source or ProductionCoordinatorNominationEntitlementFactSource()
        )

    def evaluate_organism_authority(self, request: S015AuthorityRequest) -> S015AuthorityResult:
        if type(request) is S015OwnApplicantAuthorityRequest:
            if request.action not in _OWN_ACTIONS:
                return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        elif type(request) is S015PostFoundingAuthorityRequest:
            if request.action not in _POST_ACTIONS:
                return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        else:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)

        if request.command_subtype != request.action.value:
            return self._refuse(request, S015AuthorityRefusalCode.UNRECOGNISED_ACTION)
        target_organism_matches = False
        if request.target is not None and request.living_organism_id is not None:
            if request.action in {
                OrganismAction.TRANSITION_CONTEXTUAL_ROLE,
                OrganismAction.RECORD_OFFICE_CONSENT,
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
            elif request.action in _VERSION_SPECS:
                if request.target.get("version_uuid") != request.version_uuid or request.version_kind not in {"DRAFT", "EFFECT"}:
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

        if not self._identity_active(request):
            return self._refuse(
                request,
                S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
                S015AuthorityRefusalCode.E_IDENTITY_INACTIVE,
            )

        if request.action is OrganismAction.SUBMIT_APPLICATION:
            return self._submit(request, organism)
        if request.action is OrganismAction.WITHDRAW_APPLICATION:
            return self._withdraw(request, organism)
        if request.action is OrganismAction.TRANSITION_CONTEXTUAL_ROLE:
            return self._qualify_role_proposal(request, organism)
        if request.action is OrganismAction.RECORD_OFFICE_CONSENT:
            return self._qualify_office_consent(request, organism)
        return self._coordinator_act(request, organism)

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
            self_proposal = proposal.actor_id == nominee.identity_id
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
        self_proposal = proposal.actor_id == nominee.identity_id
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

    def _refuse(
        self,
        request: S015AuthorityRequest,
        code: S015AuthorityRefusalCode,
        disclosed: S015AuthorityRefusalCode | None = None,
        *,
        facts: dict[str, object] | None = None,
    ) -> S015AuthorityResult:
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
        authority_facts = {"x": code.value}
        if facts:
            authority_facts.update(facts)
        fields["authority_reference"] = "s015a1:" + canonical_json_bytes(authority_facts).decode("utf-8")
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
        try:
            registration_id = int(registration_reference.removeprefix("identity-transition:"))
            if registration_id < 1:
                raise ValueError
        except (TypeError, ValueError):
            return self._refuse(request, S015AuthorityRefusalCode.E_NOT_REGISTERED)
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
            "r": str(registration_id),
            "s": str(settings.version_uuid),
        }
        if request.predecessor_reference is not None:
            facts["p"] = str(request.predecessor_reference)
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
        if not self._lo_capacity(organism, request):
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_NO_CAPACITY)
        lo_state = self._lo_state(organism, request)
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
        if lo_state == "DORMANT":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_PAUSED)
        if lo_state == "CLOSED":
            return self._refuse(request, S015AuthorityRefusalCode.C_LO_CLOSED)
        if lo_state != "ACTIVE" and not first_configuration and not formation_gateway_change:
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
        office = self._effective_coordinator_office(request, membership)
        if office is None:
            return self._refuse(request, S015AuthorityRefusalCode.E_NOT_EFFECTIVE_COORDINATOR)

        if request.action in _VERSION_SPECS:
            return self._qualify_gateway_change(request, organism, membership, office)
        return self._qualify_application_processing(request, organism, membership, office)

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
                "a": str(claim.pk),
                "e": str(engagement.pk),
                "t": "CQ_INACTIVE" if configuration.cq_active is not True else "SOLE_COORDINATOR",
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
                "a": str(claim.pk),
                "e": str(engagement.pk),
                "t": "CQ_INACTIVE" if configuration.cq_active is not True else "SOLE_COORDINATOR",
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
                return self._refuse(request, S015AuthorityRefusalCode.C_ROUTE_IS_CQ)
            route_basis = "CQ_INACTIVE" if configuration.cq_active is not True else "CHANGE_FLAG_NO"
        nomination_entitlement_reference = None
        if request.version_uuid is not None:
            model, prefix = _VERSION_SPECS[request.action]
            candidate = model.objects.using(self.database_alias).filter(
                version_uuid=request.version_uuid,
                living_organism=organism,
                recorded_at__lte=request.evaluated_at,
            ).first()
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
            if request.version_kind == "EFFECT" and not self._version_complete(candidate):
                return self._refuse(request, S015AuthorityRefusalCode.DENIED)
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
    def _version_complete(version) -> bool:
        if isinstance(version, LOApplicationSettingsVersion):
            return (
                version.applications_open is not None
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
    def _qualified(request: S015AuthorityRequest, facts: dict[str, object]) -> S015AuthorityResult:
        authority_reference = "s015a1:" + canonical_json_bytes(facts).decode("utf-8")
        if len(authority_reference) > 255:
            return ProductionApplicationAuthorityProvider._refusal_for_size(request)
        fields = asdict(request)
        fields["authority_reference"] = authority_reference
        if type(request) is S015OwnApplicantAuthorityRequest:
            return S015OwnApplicantAuthorityResponse(**fields)
        return S015PostFoundingAuthorityResponse(**fields)

    @staticmethod
    def _refusal_for_size(request: S015AuthorityRequest) -> S015AuthorityResult:
        fields = asdict(request)
        fields["refusal_code"] = S015AuthorityRefusalCode.DENIED
        fields["disclosed_refusal_code"] = S015AuthorityRefusalCode.DENIED
        fields["authority_reference"] = "s015a1:" + canonical_json_bytes({
            "x": S015AuthorityRefusalCode.DENIED.value,
        }).decode("utf-8")
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