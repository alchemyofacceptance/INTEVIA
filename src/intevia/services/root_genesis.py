"""One atomic root founding command under the issued disposable-alpha invocation.

This is an operator entry point, not a member-facing request or authority
provider. The issued capture names its Human issuer; bytes establish integrity,
not a cryptographic signature or authentication of the person entering them.
No environment flag, fixture grant or supplied 'qualified' boolean opens it.
"""
from dataclasses import dataclass, field
from hashlib import sha256
import json
from uuid import UUID, uuid4

from django.db import connection, transaction

from core import models
from core import root_genesis_contract as contract
from src.intevia.services.identity_service import LearnerRegistrationService
from src.intevia.services.organism_application_authority import fixture_terms_permitted
from src.intevia.services.organism_authority import canonical_json_bytes, authority_target_fingerprint
from src.intevia.services.organism_command_support import _empty_anchor_fingerprint
from src.intevia.services.organism_role_definition_service import (
    DefinitionProvision, _write_definition_version,
)


class GenesisRefused(ValueError):
    """The whole GENESIS operation is refused; no partial root is committed."""


@dataclass(frozen=True)
class GenesisAccount:
    username: str
    password: str = field(repr=False)
    terms_accepted: bool = False


@dataclass(frozen=True)
class GenesisCommand:
    organism_uuid: UUID
    founder: GenesisAccount
    nominee: GenesisAccount
    terms_version_uuid: UUID
    genesis_terms_accepted: bool
    request_reference: str
    idempotency_key: str
    principal_invocation: bytes = field(repr=False)
    designation_invocation: bytes = field(repr=False)
    adoption_capture: bytes = field(repr=False)
    purpose: str = 'INTEVIA root Living Organism'


def _now():
    with connection.cursor() as cursor:
        cursor.execute('SELECT statement_timestamp()')
        return cursor.fetchone()[0]


def _fingerprint(token, key):
    return _empty_anchor_fingerprint(database_alias='default', anchor_token=token, anchor_uuid=key)


def _cache(anchor, key, at):
    return dict(current_state_effective_at=at, state_known_at=at,
                state_source_event_set_fingerprint=_fingerprint(anchor, key))


def _append(anchor, actor, basis, decision, fp, key, at, action, outcome, evidence, metadata=None):
    """All new S015 chain writes use the shared writer with the observed head."""
    token = anchor._meta.db_table
    # Event uniqueness is (actor, action, key), across all anchors of a table.
    event_key = "r1:" + sha256(canonical_json_bytes([key, token, anchor.pk, action])).hexdigest()
    with connection.cursor() as cursor:
        cursor.execute('SELECT event_table,fk_col FROM public.s015_anchor_map() WHERE anchor_token=%s',[token])
        table,fk = cursor.fetchone()
        # Both identifiers come only from the installed closed anchor map.
        cursor.execute(f'SELECT id,sequence FROM public."{table}" WHERE "{fk}"=%s ORDER BY sequence DESC LIMIT 1',[anchor.pk])
        head = cursor.fetchone()
        cursor.execute('''SELECT public.s015_append_event(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,
            NULL,%s,%s,%s::integer,%s,NULL,%s)''',[
            token,anchor.pk,action,outcome,at,at,at,actor.pk,basis.pk,decision,evidence,key,fp,
            json.dumps(metadata or {},default=str),uuid4(),head[0] if head else None,head[1] if head else None,
            actor.access_epoch,event_key])
        return cursor.fetchone()[0]


def _refresh(anchor, at):
    with connection.cursor() as cursor:
        cursor.execute('SELECT public.s015_refresh_governed_cache(%s,%s,%s,statement_timestamp())',
                       [anchor._meta.db_table,anchor.pk,at])
    anchor.refresh_from_db()


def _check(command):
    if type(command) is not GenesisCommand or type(command.organism_uuid) is not UUID:
        raise GenesisRefused('GENESIS requires the issued command and a new root UUID.')
    for raw, expected in ((command.principal_invocation,contract.PRINCIPAL_SHA256),
                          (command.designation_invocation,contract.DESIGNATION_SHA256),
                          (command.adoption_capture,contract.ADOPTION_SHA256)):
        if type(raw) is not bytes or sha256(raw).hexdigest()!=expected:
            raise GenesisRefused('GENESIS invocation bytes do not match the issued evidence; nothing was created.')
    for value,maximum in ((command.request_reference,128),(command.idempotency_key,120),(command.purpose,4000)):
        if type(value) is not str or not value.strip() or len(value)>maximum:
            raise GenesisRefused('GENESIS request identity and purpose must be non-empty and within their field limits.')
    if command.genesis_terms_accepted is not True:
        raise GenesisRefused('GENESIS needs the founder’s explicit acceptance of its platform terms.')
    for account in (command.founder,command.nominee):
        if type(account) is not GenesisAccount or account.terms_accepted is not True:
            raise GenesisRefused('Both alpha registrations need their own explicit test terms acceptance.')
    if not fixture_terms_permitted('default'):
        raise GenesisRefused('This issued invocation is for disposable alpha only; beta requires a fresh invocation.')


def execute(command: GenesisCommand) -> models.LivingOrganism:
    """Create the P1 foundation; all failures roll back accounts and root together.

    Every repeat refuses, including the same key. There is no second root or
    hidden re-execution. No credentials are stored in the request evidence.
    The caller must use an owned disposable run database; the existing fixture
    name check alone does not prove ownership. No real account is created by
    importing, migrating or distributing this implementation.
    """
    _check(command)
    if connection.in_atomic_block:
        raise GenesisRefused('GENESIS owns the outer transaction; call it outside an existing transaction.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)',[int(sha256(b'INTEVIA:R:GENESIS:1').hexdigest()[:15],16)])
        if models.RootGenesis.objects.exists() or models.LivingOrganism.objects.filter(platform_root=True).exists():
            raise GenesisRefused('GENESIS already exists. No second root or account batch was created.')
        terms = models.PlatformTermsVersion.objects.filter(terms_version_uuid=command.terms_version_uuid).first()
        if terms is None or not terms.is_fixture:
            raise GenesisRefused('The issued alpha operation requires the explicitly disposable terms version.')
        people=[]
        for account,name in ((command.founder,'Carmian Owen'),(command.nominee,'Debra Reyes')):
            registered=LearnerRegistrationService.register(username=account.username,password=account.password,
                display_name=name,terms_version_uuid=command.terms_version_uuid,terms_accepted=account.terms_accepted,
                correlation_id=uuid4(),entry_mode=models.LearnerRegistration.EntryMode.SUPPORTED,
                enterer_executor='management:provision_learner')
            people.append(models.Identity.objects.get(pk=registered.identity_id))
        founder,nominee=people
        at=_now()
        request=dict(schema='R:GENESIS:1',canonical_reference=contract.CANONICAL_ROOT,scope=contract.SCOPE,
            organism_uuid=str(command.organism_uuid),founder=str(founder.identity_id),nominee=str(nominee.identity_id),
            founder_username=founder.canonical_username,nominee_username=nominee.canonical_username,
            terms_version_uuid=str(command.terms_version_uuid),request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,principal_sha256=contract.PRINCIPAL_SHA256,
            designation_sha256=contract.DESIGNATION_SHA256,adoption_sha256=contract.ADOPTION_SHA256,purpose=command.purpose)
        raw=canonical_json_bytes(request); fp=sha256(raw).hexdigest()
        principal,created=models.AuthorityPrincipal.objects.get_or_create(
            governed_source_namespace=contract.PRINCIPAL_NAMESPACE,
            defaults=dict(canonical_governed_source_id=contract.PRINCIPAL_SOURCE,
                          bootstrap_invocation_fingerprint=contract.PRINCIPAL_SHA256,display_label='Carmian Owen, Human Governor'))
        if principal.canonical_governed_source_id!=contract.PRINCIPAL_SOURCE or principal.bootstrap_invocation_fingerprint!=contract.PRINCIPAL_SHA256:
            raise GenesisRefused('The recorded authority principal differs from the verified Human invocation.')
        basis=models.AuthorityBasis.objects.create(authority_principal=principal,derivation_root_principal=principal,
            issuer=founder,authority_class='GENESIS',scope_fingerprint=fp,
            instrument_reference='sha256:'+contract.DESIGNATION_SHA256,evidence_reference='sha256:'+contract.ADOPTION_SHA256,
            issued_at=at,effective_at=at,received_at=at)
        target=dict(organism_uuid=str(command.organism_uuid),request_fingerprint=fp)
        target_bytes=canonical_json_bytes(target)
        payload=dict(action='GENESIS',actor=str(founder.identity_id),outcome='QUALIFIED',request_fingerprint=fp,
                     effective_at=at,authority='sha256:'+contract.ADOPTION_SHA256,target=target)
        payload_bytes=canonical_json_bytes(payload)
        decision='s015d1:'+sha256(b'INTEVIA:S015:ORGANISM_AUTHORITY_DECISION:v1\0'+payload_bytes).hexdigest()
        models.AuthorityDecision.objects.create(decision_reference=decision,decision_payload=json.loads(payload_bytes),
            decision_payload_bytes=payload_bytes,request_kind='RootGenesisCommand',action='GENESIS',command_subtype='GENESIS',
            actor=founder,actor_identity_id=founder.identity_id,actor_access_epoch=founder.access_epoch,
            actor_credential_pk=founder.credential_id,target=target,target_bytes=target_bytes,
            target_fingerprint=authority_target_fingerprint(target),effective_at=at,evaluated_at=at,
            authority_reference='sha256:'+contract.ADOPTION_SHA256,facts_read=[{'source':'Human-issued alpha designation','independent':False}],
            outcome='QUALIFIED',refusal_code=None,provider_identity='INTEVIA:R:issued-genesis',provider_version='1',
            request_reference=command.request_reference,idempotency_key=command.idempotency_key,recorded_at=None)
        genesis=models.RootGenesis.objects.create(canonical_reference=contract.CANONICAL_ROOT,organism_uuid=command.organism_uuid,
            scope=contract.SCOPE,founder=founder,nominee=nominee,basis=basis,principal_invocation=command.principal_invocation,
            designation_invocation=command.designation_invocation,adoption_capture=command.adoption_capture,
            request_bytes=raw,request_fingerprint=fp,decision_reference=decision,request_reference=command.request_reference,
            idempotency_key=command.idempotency_key,effective_at=at,received_at=at)
        content_uuid=uuid4()
        lo=models.LivingOrganism.objects.create(organism_id=command.organism_uuid,slug='intevia',platform_root=True,
            state='FOUNDING_PENDING',constitutional_spine_reference=f'founding-content:{content_uuid}',
            **_cache('core_livingorganism',command.organism_uuid,at))
        models.LOFoundingContentVersion.objects.create(content_uuid=content_uuid,living_organism=lo,version=1,
            name='INTEVIA',purpose=command.purpose,actor_identity=founder,actor_access_epoch=founder.access_epoch,
            authority_decision_reference=decision,effective_at=at,received_at=at,recorded_at=None,content_commitment=None)
        acceptance_uuid=uuid4()
        acceptance=dict(terms_version_uuid=terms.terms_version_uuid,acceptance_kind='GENESIS',
            subject_reference=f'living-organism:{lo.organism_id}',accepted_by_identity_id=founder.identity_id,
            accepted_by_access_epoch=founder.access_epoch,accepted_at=at)
        models.PlatformTermsAcceptance.objects.create(acceptance_uuid=acceptance_uuid,terms_version=terms,
            acceptance_kind='GENESIS',subject_reference=acceptance['subject_reference'],accepted_by_identity=founder,
            accepted_by_access_epoch=founder.access_epoch,accepted_at=at,received_at=at,
            acceptance_commitment='s015r1:'+sha256(canonical_json_bytes(acceptance)).hexdigest())
        event_id=_append(lo,founder,basis,decision,fp,command.idempotency_key,at,'FOUND','ACTIVE',f'founding-content:{content_uuid}')
        found=models.LivingOrganismEvent.objects.get(pk=event_id)
        batch='founding-batch:'+str(found.event_uuid)
        membership_uuid=uuid4()
        member=models.OrganismMembership.objects.create(membership_uuid=membership_uuid,identity=founder,living_organism=lo,
            state='ACTIVE',**_cache('core_organismmembership',membership_uuid,at))
        definitions={}
        for code in (*contract.RESERVED,'COORDINATOR'):
            ordinary=code=='COORDINATOR'
            definitions[code]=_write_definition_version(database_alias='default',organism=lo,code=code,
                kind='COORDINATOR' if ordinary else 'BOARD_OFFICE',appointing_authority='LO_COORDINATOR_BODY' if ordinary else 'NAMED_BODY',
                appointing_body_reference=None if ordinary else 'INTEVIA:reserved-constitutional-route',alternative_route_reference=None,
                nomination_basis='SILENT' if ordinary else 'CONSTITUTION',nomination_policy='C' if ordinary else 'UNSUPPORTED',
                nomination_dependency=None if ordinary else 'R:reserved-succession-route',participation_required=False,
                ex_officio_seat_reference=None,supersedes=None,enterer=founder,authority_decision_reference=decision,
                occurred_at=at,effective_at=at,received_at=at,provisions=() if ordinary else tuple(
                    DefinitionProvision(rel,'sha256:'+contract.DESIGNATION_SHA256,'R:root-designation:1')
                    for rel in ('OFFICE_BASIS','APPOINTING_AUTHORITY','NOMINATION_ROUTE')))
        assignments=[]
        for code in contract.FOUNDER_OFFICES:
            assignment_uuid=uuid4()
            assignments.append(models.ContextualRoleAssignment.objects.create(assignment_uuid=assignment_uuid,
                membership=member,role_definition=definitions[code],state='ACTIVE',circle=None,
                **_cache('core_contextualroleassignment',assignment_uuid,at)))
        # Root's same-transaction completion requires the founder assignment anchor.
        # Its deferred structural and authoritative-state checks still run at commit.
        models.LivingOrganism.objects.filter(pk=lo.pk).update(founder_identity=founder,founding_authority_basis=basis)
        lo.refresh_from_db()
        for action,outcome in (('OPEN','PROPOSED'),('ACTIVATE','ACTIVE')):
            _append(member,founder,basis,decision,fp,command.idempotency_key,at,action,outcome,batch,{'reason_class':'FOUNDING'})
        _refresh(member,at)
        for assignment in assignments:
            _append(assignment,founder,basis,decision,fp,command.idempotency_key,at,'ASSIGN','ACTIVE',batch)
            _refresh(assignment,at)
        for code in contract.SUCCESSOR_OFFICES:
            models.RootSuccessorNomination.objects.create(genesis=genesis,definition=definitions[code],nominee=nominee,
                entered_by=founder,effective_at=at)
        models.EssentialCoverageRequirement.objects.create(living_organism=lo,roster_version=1,
            responsibility_domain_code='GOVERNANCE',qualifying_role_code='COORDINATOR',minimum_active_occupants=1,
            scope='LIVING_ORGANISM',effective_from=at)
        coverage=models.EssentialCoverageCase.objects.create(living_organism=lo)
        known=_now()
        _append(coverage,founder,basis,decision,fp,command.idempotency_key,at,'ASSESS','SATISFIED',batch,
                dict(roster_version=1,evaluated_state_at=at.isoformat(),evaluated_known_at=known.isoformat(),determiner_capacity_reference=batch))
        _refresh(lo,at)
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        return lo
