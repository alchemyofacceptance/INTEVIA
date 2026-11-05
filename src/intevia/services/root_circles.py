"""R's initial inventory uses the ordinary GENERIC Circle route and unchanged SE checks."""
from dataclasses import dataclass
from uuid import UUID,uuid4,uuid5
from django.db import connections
from core import models
from core.organism_contract import OrganismAction
from src.intevia.services import circle_commands as circles
from src.intevia.services import organism_application_service as applications
from src.intevia.services.organism_authority import S015Authority
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_entitlement_service import check_allowance
from src.intevia.services.organism_formation_service import (
    OrganismPhaseAdvanceService,PhaseAdvanceCommand,OrganismBirthingService,BirthingCommand)

INVENTORY=(
 ('INTEVIA Education','Accept other organisations’ courses for public availability, release by release, without implying accreditation.'),
 ('INTEVIA Library','Accept library resources for public availability; translation activities sit here.'),
 ('INTEVIA Support','Handle support requests, escalated moderation, help requests, and questions and suggestions about INTEVIA, particularly during testing.'),
 ('INTEVIA Relationships','Handle subscription questions, trial extensions and external sales enquiries; share fundraising with Finance and community work with Marketing and Communications.'),
 ('INTEVIA Operations','Carry out operational work, including procurement and facilities.'),
 ('INTEVIA People','Human resources for INTEVIA.'),
 ('INTEVIA Finance','Accounts, budgets, payments, subscription income, grants and funding records; share fundraising with Relationships.'),
 ('INTEVIA Legal and Compliance','Contracts, terms, privacy and data protection, trademarks, regulatory filings and risk; the Privacy Coordinator works here.'),
 ('INTEVIA Technology','Build and run the platform: product, releases, IT and security.'),
 ('INTEVIA Marketing and Communications','Brand, public communication, newsletters and launches; share community work with Relationships.'),
)

def now(alias='default'):
    with connections[alias].cursor() as cursor:
        cursor.execute('SELECT statement_timestamp()')
        return cursor.fetchone()[0]


def _founder(alias,lo,actor,at):
    if not lo.platform_root or not circles.qualified_root(alias,lo,at):
        circles.unavailable()
    if not models.RootGenesis.objects.using(alias).filter(organism_uuid=lo.organism_id,founder=actor).exists():
        circles.unavailable()
    circles.lo_source(alias,lo,lo.organism_id,'R:ROOT_CIRCLES',actor,at)


@dataclass(frozen=True)
class RootBirthingInputs:
    """Explicit ordinary settings/election inputs; no product defaults or fabricated acceptance."""
    governance: dict
    application_settings: dict
    application_question: dict
    offering_choice: str
    policy_version: int
    policy_text_sha256: str


def prepare_birthing(*,credential,organism_uuid,inputs:RootBirthingInputs,alias='default'):
    """Compose existing commands for a fresh alpha foundation, each with its own receipt.

    These are separate committed governed acts. On failure their recorded progress
    remains; resume through the ordinary commands using their receipts. This helper
    does not claim all GENESIS phases are one database transaction. No package grant
    or fictional terms record is written here. The caller supplies accepted policy.
    """
    if type(inputs) is not RootBirthingInputs:raise TypeError('RootBirthingInputs required')
    with circles.command_context(alias,credential,organism_uuid,require_member=True) as (lo,actor,at):
        _founder(alias,lo,actor,at)
        if models.LivingOrganismEvent.objects.using(alias).filter(living_organism=lo,action__in=('PHASE_ADVANCE','BIRTHING')).exists():
            raise circles.CircleAccessRefused('This preparation already has recorded progress. Resume its ordinary governed commands; do not repeat fresh preparation.')
    clock=lambda:now(alias)
    phases=OrganismPhaseAdvanceService(clock=clock,database_alias=alias)
    def phase(source,target):
        key=uuid4().hex
        return phases.advance_phase(PhaseAdvanceCommand(credential=credential,living_organism_uuid=organism_uuid,
            from_phase=source,to_phase=target,request_reference='r-phase:'+key,idempotency_key=key))
    phase('CONCEIVING','GERMINATING')
    authority=S015Authority(provider=ProductionApplicationAuthorityProvider(database_alias=alias),database_alias=alias)
    gateway=applications.OrganismApplicationService(authority=authority,clock=clock,database_alias=alias)
    for action,fields in ((OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION,inputs.governance),
                         (OrganismAction.EFFECT_APPLICATION_SETTINGS,inputs.application_settings),
                         (OrganismAction.EFFECT_APPLICATION_QUESTION,inputs.application_question)):
        at=clock();key=uuid4().hex
        draft=gateway.save_gateway_draft(applications.GatewayDraftCommand(credential=credential,living_organism_id=organism_uuid,
            action=action,fields=fields,request_reference='r-draft:'+key,idempotency_key=key,occurred_at=at,effective_at=at))
        at=clock();key=uuid4().hex
        gateway.effect_gateway_version(applications.EffectGatewayVersionCommand(credential=credential,living_organism_id=organism_uuid,
            action=action,version_uuid=draft.version_uuid,request_reference='r-effect:'+key,idempotency_key=key,occurred_at=at,effective_at=at))
    phase('GERMINATING','BIRTHING')
    key=uuid4().hex
    return OrganismBirthingService(clock=clock,database_alias=alias).birth_living_organism(BirthingCommand(
        credential=credential,living_organism_uuid=organism_uuid,request_reference='r-birthing:'+key,idempotency_key=key,
        offering_choice=inputs.offering_choice,policy_version=inputs.policy_version,policy_text_sha256=inputs.policy_text_sha256))


def birth_inventory(*,credential,organism_uuid,request_uuid:UUID,alias='default'):
    """All ten births/descriptions and the completion receipt commit or roll back together."""
    if connections[alias].in_atomic_block:
        raise circles.CircleAccessRefused("Call birth_inventory outside an existing transaction so it can commit the batch.")
    if type(request_uuid) is not UUID:raise TypeError('request_uuid must be a UUID')
    with circles.command_context(alias,credential,organism_uuid,require_member=True) as (lo,actor,at):
        _founder(alias,lo,actor,at)
        prior=models.RootCircleBatch.objects.using(alias).filter(living_organism=lo).first()
        if prior:
            if prior.batch_uuid!=request_uuid:raise circles.CircleAccessRefused('The initial Circle inventory is already recorded.')
            return prior
        if not models.LivingOrganismEvent.objects.using(alias).filter(living_organism=lo,action='BIRTHING',effective_at__lte=at,recorded_at__lte=at).exists():
            raise circles.CircleAccessRefused('Complete the ordinary Birthing and accepted SE election before the initial Circle inventory.')
        allowance=check_allowance(lo.organism_id,'circles',10,at,database_alias=alias)
        if allowance.status!='within':raise circles.CircleAccessRefused(allowance.message)
        records=[]
        for name,purpose in INVENTORY:
            circle=circles.form(credential=credential,organism_uuid=organism_uuid,
                circle_uuid=uuid5(lo.organism_id,'R:initial:'+name),kind='GENERIC',name=name,purpose=purpose,
                request_uuid=uuid5(request_uuid,name),alias=alias)
            records.append(dict(name=name,circle_uuid=str(circle.circle_uuid)))
        result=models.RootCircleBatch.objects.using(alias).create(batch_uuid=request_uuid,living_organism=lo,
            enterer=actor,circles=records,effective_at=now(alias))
        with connections[alias].cursor() as cursor:cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        return result
