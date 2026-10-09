from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import uuid

from django.db import connection, transaction
from django.test import override_settings

from core.models import (
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    LivingOrganismEvent,
    LivingOrganism,
    LOGovernanceConfigurationVersion,
    LOApplicationQuestionVersion,
    LOApplicationSettingsVersion,
    OrganismMembership,
    OrganismRoleDefinition,
    PlatformTermsVersion,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_application_service import (
    EffectGatewayVersionCommand,
    GatewayDraftCommand,
    OrganismApplicationService,
)
from src.intevia.services.organism_authority import S015Authority
from src.intevia.services.organism_formation_service import (
    BirthingCommand,
    FoundLivingOrganismCommand,
    OrganismBirthingService,
    OrganismFormationService,
    OrganismPhaseAdvanceService,
    PhaseAdvanceCommand,
)
from tests.s015_0021_support import (
    append_event,
    empty_fp,
    fresh,
    seed_fixture_substitute_authority,
    seed_identity,
    register_test_learner,
    refresh,
    u,
)

SB2_TEST_PASSWORD = "Existing-Governed-Password-1"


@dataclass(slots=True)
class BirthingRoute:
    tag: str
    terms: PlatformTermsVersion
    founder: Identity
    organism: LivingOrganism
    formation_service: OrganismFormationService
    phase_service: OrganismPhaseAdvanceService
    application_service: OrganismApplicationService
    birthing_service: OrganismBirthingService


@dataclass(frozen=True, slots=True)
class RouteRecordedTimes:
    founding_effective_at: datetime
    founding_recorded_at: datetime
    prebirthing_effective_at: datetime
    prebirthing_recorded_at: datetime
    birthing_effective_at: datetime | None
    birthing_recorded_at: datetime | None
    latest_effective_at: datetime
    latest_recorded_at: datetime


def database_now():
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def route_test_now(route: BirthingRoute) -> datetime:
    times = route_recorded_times(route)
    return max(database_now(), times.latest_effective_at, times.latest_recorded_at)


def route_recorded_times(route: BirthingRoute) -> RouteRecordedTimes:
    events = list(
        LivingOrganismEvent.objects.filter(living_organism=route.organism)
        .order_by("sequence")
    )
    founding_event = next((event for event in events if event.sequence == 1), None)
    birthing_event = next((event for event in events if event.action == "BIRTHING"), None)
    prebirthing_events = [event for event in events if event.action != "BIRTHING"]
    if founding_event is None or not prebirthing_events:
        raise RuntimeError("route has no recorded founding or pre-Birthing event")
    if any(event.recorded_at is None for event in events):
        raise RuntimeError("route time basis contains an unrecorded event")
    latest_event = max(events, key=lambda event: (event.recorded_at, event.sequence))
    prebirthing_event = max(
        prebirthing_events,
        key=lambda event: (event.recorded_at, event.sequence),
    )
    relevant_events = [founding_event, latest_event, prebirthing_event]
    if birthing_event is not None:
        relevant_events.append(birthing_event)
    if any(
        event.effective_at is None or event.recorded_at is None
        for event in relevant_events
    ):
        raise RuntimeError("route time basis contains an unrecorded event coordinate")
    return RouteRecordedTimes(
        founding_effective_at=founding_event.effective_at,
        founding_recorded_at=founding_event.recorded_at,
        prebirthing_effective_at=prebirthing_event.effective_at,
        prebirthing_recorded_at=prebirthing_event.recorded_at,
        birthing_effective_at=None if birthing_event is None else birthing_event.effective_at,
        birthing_recorded_at=None if birthing_event is None else birthing_event.recorded_at,
        latest_effective_at=latest_event.effective_at,
        latest_recorded_at=latest_event.recorded_at,
    )


def _make_terms(tag: str) -> PlatformTermsVersion:
    body = f"Labelled SB-2 fixture terms for {tag}."
    return PlatformTermsVersion.objects.create(
        terms_version_uuid=uuid.uuid4(),
        version_label="FIXTURE-DEV-1",
        text_commitment="s015r1:" + hashlib.sha256(body.encode("utf-8")).hexdigest(),
        text_body=body,
        cited_rulings=[],
        published_by_authority_reference="fixture:development",
        is_fixture=True,
        effective_from=database_now() - timedelta(days=1),
    )


def prepare_route(
    tag: str | None = None,
    *,
    founder: Identity | None = None,
    governance_enabled: bool | None = False,
    include_settings: bool = True,
    include_question: bool = True,
    to_birthing_phase: bool = True,
) -> BirthingRoute:
    tag = tag or fresh()
    with override_settings(INTEVIA_FIXTURE_SUBSTITUTES_PERMITTED=True):
        terms = _make_terms(tag)
        founder = founder or register_test_learner(
            username=f"sb2-founder-{tag}",
            password=SB2_TEST_PASSWORD,
            display_name=f"SB-2 Founder {tag}",
            terms_version=terms,
        )
        with transaction.atomic(), connection.cursor() as cursor:
            seed_fixture_substitute_authority(cursor, f"sb2-founder-{tag}")
        provider = ProductionApplicationAuthorityProvider()
        formation_service = OrganismFormationService(
            clock=database_now,
            authority=S015Authority(provider=provider),
        )
        found = formation_service.found_living_organism(FoundLivingOrganismCommand(
            credential=founder.credential,
            name=f"SB-2 Living Organism {tag}",
            purpose=f"Exercise the commissioned SB-2 route {tag}.",
            request_reference=f"sb2:found:{tag}",
            idempotency_key=f"sb2:found:key:{tag}",
            accepted_terms_version_uuid=terms.terms_version_uuid,
        ))

    organism = LivingOrganism.objects.get(organism_id=found.living_organism_uuid)
    phase_service = OrganismPhaseAdvanceService(clock=database_now)
    phase_at = database_now()
    phase_service.advance_phase(PhaseAdvanceCommand(
        credential=founder.credential,
        living_organism_uuid=organism.organism_id,
        from_phase="CONCEIVING",
        to_phase="GERMINATING",
        request_reference=f"sb2:phase-one:{tag}",
        idempotency_key=f"sb2:phase-one:key:{tag}",
        effective_at=phase_at,
    ))

    application_service = OrganismApplicationService(clock=database_now)
    if governance_enabled is not None:
        effect_gateway(
            application_service,
            founder,
            organism,
            tag,
            OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,
            {
                "cq_active": False,
                "governance_enabled": governance_enabled,
                "inherit_portal_cq_settings": False,
                "require_cq_to_change_lo_settings": False,
                "require_cq_to_change_application_question": False,
            },
        )
    if include_settings:
        effect_gateway(
            application_service,
            founder,
            organism,
            tag,
            OrganismAction.EFFECT_APPLICATION_SETTINGS,
            {
                "applications_open": True,
                "automatic_acceptance": False,
                "processing_responsibility": "LO",
            },
        )
    if include_question:
        effect_gateway(
            application_service,
            founder,
            organism,
            tag,
            OrganismAction.EFFECT_APPLICATION_QUESTION,
            {"answer_format": "LONG_ANSWER"},
        )
    if to_birthing_phase:
        phase_at = database_now()
        phase_service.advance_phase(PhaseAdvanceCommand(
            credential=founder.credential,
            living_organism_uuid=organism.organism_id,
            from_phase="GERMINATING",
            to_phase="BIRTHING",
            request_reference=f"sb2:phase-two:{tag}",
            idempotency_key=f"sb2:phase-two:key:{tag}",
            effective_at=phase_at,
        ))

    return BirthingRoute(
        tag=tag,
        terms=terms,
        founder=founder,
        organism=organism,
        formation_service=formation_service,
        phase_service=phase_service,
        application_service=application_service,
        birthing_service=OrganismBirthingService(clock=database_now),
    )


def effect_gateway(
    service: OrganismApplicationService,
    founder: Identity,
    organism: LivingOrganism,
    tag: str,
    action: OrganismAction,
    fields: dict[str, object],
):
    draft_at = database_now()
    draft = service.save_gateway_draft(GatewayDraftCommand(
        credential=founder.credential,
        living_organism_id=organism.organism_id,
        action=action,
        request_reference=f"sb2:draft:{action.value}:{tag}",
        idempotency_key=f"sb2:draft:key:{action.value}:{tag}",
        occurred_at=draft_at,
        effective_at=draft_at,
        received_at=database_now(),
        fields=fields,
    ))
    effect_at = database_now()
    return service.effect_gateway_version(EffectGatewayVersionCommand(
        credential=founder.credential,
        living_organism_id=organism.organism_id,
        action=action,
        version_uuid=draft.version_uuid,
        request_reference=f"sb2:effect:{action.value}:{tag}",
        idempotency_key=f"sb2:effect:key:{action.value}:{tag}",
        occurred_at=effect_at,
        effective_at=effect_at,
        received_at=database_now(),
    ))


def birth(route: BirthingRoute, *, suffix: str = "birth"):
    received_at = database_now()
    return route.birthing_service.birth_living_organism(BirthingCommand(
        credential=route.founder.credential,
        living_organism_uuid=route.organism.organism_id,
        request_reference=f"sb2:{suffix}:{route.tag}",
        idempotency_key=f"sb2:{suffix}:key:{route.tag}",
        effective_at=received_at,
    ))


def route_fixture_state(route: BirthingRoute):
    organism = LivingOrganism.objects.get(pk=route.organism.pk)
    membership = OrganismMembership.objects.get(
        living_organism=organism,
        identity=route.founder,
    )
    role = OrganismRoleDefinition.objects.get(
        living_organism=organism,
        code="COORDINATOR",
        scope=OrganismRoleDefinition.Scope.LIVING_ORGANISM,
    )
    assignment = ContextualRoleAssignment.objects.get(
        membership=membership,
        role_definition=role,
        circle__isnull=True,
    )
    assignment_event = ContextualRoleAssignmentStateEvent.objects.get(
        assignment=assignment,
        sequence=1,
    )
    founding_event = LivingOrganismEvent.objects.get(
        living_organism=organism,
        sequence=1,
    )
    head_event = LivingOrganismEvent.objects.filter(
        living_organism=organism,
    ).order_by("-sequence").first()
    if head_event is None:
        raise RuntimeError("service-founded route has no LO event head")
    settings_version = LOApplicationSettingsVersion.objects.get(living_organism=organism)
    question_version = LOApplicationQuestionVersion.objects.get(living_organism=organism)
    governance_version = LOGovernanceConfigurationVersion.objects.get(living_organism=organism)
    ids = {
        "identity": route.founder.pk,
        "lo": organism.pk,
        "organism_uuid": organism.organism_id,
        "basis": organism.founding_authority_basis_id,
        "membership": membership.pk,
        "founder_membership_uuid": membership.membership_uuid,
        "roles": {"COORDINATOR": role.pk},
        "coordinator_role_uuid": role.role_uuid,
        "assignments": {"COORDINATOR": assignment.pk},
        "founding_assignment_uuid": assignment.assignment_uuid,
        "assignment_events": {"COORDINATOR": assignment_event.pk},
        "founding_event_uuid": founding_event.event_uuid,
        "founding_authority_decision_reference": founding_event.authority_decision_reference,
        "lo_event": founding_event.pk,
        "events": {"core_livingorganismevent": head_event.pk},
    }
    gateway = {
        "core_loapplicationsettingsversion": settings_version.pk,
        "core_loapplicationquestionversion": question_version.pk,
        "core_logovernanceconfigurationversion": governance_version.pk,
        "settings_basis": (
            f"governance-configuration-version:{governance_version.version_uuid};"
            f"application-settings-version:{settings_version.version_uuid}"
        ),
        "head_event": head_event.pk,
        "head_sequence": head_event.sequence,
    }
    ids["recorded_times"] = route_recorded_times(route)
    return ids, gateway


def add_active_elector(route: BirthingRoute, tag: str | None = None) -> Identity:
    tag = tag or fresh()
    times = route_recorded_times(route)
    effective_at = times.latest_effective_at
    known_at = max(database_now(), times.latest_recorded_at)
    with transaction.atomic(), connection.cursor() as cursor:
        identity_pk = seed_identity(cursor, f"sb2-elector-{route.tag}-{tag}")
        membership_uuid = u(f"sb2-membership:{route.tag}:{tag}")
        cursor.execute(
            "INSERT INTO public.core_organismmembership "
            "(membership_uuid, state, created_at, current_state_effective_at, state_known_at, "
            "state_source_event_set_fingerprint, identity_id, living_organism_id) "
            "VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
            (
                membership_uuid,
                effective_at,
                known_at,
                empty_fp(cursor, "core_organismmembership", membership_uuid),
                identity_pk,
                route.organism.pk,
            ),
        )
        membership_id = cursor.fetchone()[0]
        append_event(
            cursor,
            "core_organismmembershiptransition",
            membership_id,
            1,
            "JOIN",
            None,
            "ACTIVE",
            identity_pk,
            route.organism.founding_authority_basis_id,
            f"sb2-elector:{route.tag}:{tag}",
            eff=effective_at,
            occ=effective_at,
            rec=known_at,
            extra={"reason_class": "FOUNDING"},
        )
        refresh(cursor, "core_organismmembership", membership_id, effective_at)
    return Identity.objects.select_related("credential").get(pk=identity_pk)
