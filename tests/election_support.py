"""Small real ELECT journeys using the existing law, forum, office and seat writers."""

import json
from dataclasses import replace
from uuid import uuid4

from core import models
from src.intevia.services import cq, cq_reads, elections
from tests import cq_authority_support as board
from tests import s015_0021_support as support
from tests import test_law_acts as law
from tests import test_sr_office_actions as office

ARTICLE = "Article 7"


def provision(owner, lo, rules, *, vacancies=None, recognitions=None, resignations=None):
    body = {"elections": rules}
    if vacancies is not None:
        body["vacancies"] = vacancies
    if recognitions is not None:
        body["recognitions"] = recognitions
    if resignations is not None:
        body["resignations"] = resignations
    doc = law.Document(owner)
    version = doc.service.create_successor_version(
        identity=doc.author, resource_id=doc.name, content=json.dumps(body),
        evidence_reference="fixture:election:constitution",
    )
    doc.transition("publish_library_resource")
    proposal = replace(law.adoption(lo, doc, "ARTICLE"), version=version.version_number)
    result = law.law.OrganismLawService().adopt_law(law.command(owner, proposal))
    if result.status != "ADOPT":
        raise AssertionError(f"Constitution adoption returned {result.status}")
    return {"binding_uuid": proposal.proposal, "version_id": version.pk}


def owner(*, seats=2, elected=(1,), rules=None, voting=True, vacancies=None):
    lo, founder = law.ordinary_birth(offering_choice="FREE", board_seats=seats, elected=elected)
    circle = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
    citation = provision(founder, lo, rules or {ARTICLE: seat()}, vacancies=vacancies)
    scope = {"credential": founder.credential, "organism_uuid": lo.organism_id}
    if voting:
        enable_voting(scope)
    return lo, founder, scope, circle, citation


def enable_voting(scope):
    cq.change_settings(**scope, changes={"clarity_stage": False, "enabled": True},
                       predecessor=None, request_uuid=uuid4())


def seat(**changes):
    return {"type": "SEAT", "place": "trustee-1", "body": "LO_MEMBERSHIP"} | changes


def call(case, scope, citation, *, provision=ARTICLE, place="trustee-1"):
    proposal = elections.attachment(organism_uuid=scope["organism_uuid"], **citation, provision=provision, place=place)
    journey, _, topic = case.topic(scope, title="Elect to " + place, markdown=proposal["text"])
    elections.call(**journey, **citation, request_uuid=uuid4(), version_uuid=topic["version"], provision=provision, place=place)
    return journey


def nominate(journey, nominee, *, by=None, assignment=None):
    return elections.nominate(**(journey | {"credential": (by or nominee).credential}),
        request_uuid=uuid4(), person_uuid=nominee.identity_id, assignment_uuid=assignment,
        statement="I put my name forward for the stated term." if by is None else "")


def open_election(journey):
    cq.mark_prepared(**journey, request_uuid=uuid4())
    return cq_reads.vote_status(**journey)["ballot"]


def cast(journey, ballot, person, choice="QUALIFIED", request=None):
    return cq.answer(**(journey | {"credential": person.credential}), ballot_uuid=ballot,
                     choice=choice, request_uuid=request or uuid4())


def define_office(lo, founder, place, body, provision_name, citation, *, board_members):
    from src.intevia.services import organism_role_definition_service as definitions

    fx = office.SROfficeActionTests()
    fx.actor, fx.organism = founder, lo
    authority = board.authority()
    version = str(citation["version_id"])
    keys = ["OFFICE_BASIS", "APPOINTING_AUTHORITY"]
    if board_members:
        keys.append("PARTICIPATION_REQUIRED")
    definition = definitions.DefinitionContent(
        code=place, appointing_authority="NAMED_BODY", appointing_body_reference="body:" + body,
        participation_required=board_members,
        provisions=tuple(definitions.DefinitionProvision(k, provision_name, version) for k in keys),
    )
    result = definitions.OrganismRoleDefinitionService(authority=authority, clock=support.database_now).record_role_definition(
        fx._command(content=definition))
    role = models.OrganismRoleDefinition.objects.get(role_uuid=result.role_definition_uuid)
    return role


def office_proposal(lo, founder, member, role, *, self_proposed):
    from src.intevia.services.organism_office_consent_service import (
        OfficeAppointmentConsentService,
    )
    from src.intevia.services.organism_role_service import OrganismRoleService

    fx = office.SROfficeActionTests()
    fx.actor, fx.organism = founder, lo
    authority = board.authority()
    membership = models.OrganismMembership.objects.get(living_organism=lo, identity=member)
    roles = OrganismRoleService(authority=authority, clock=support.database_now)
    proposed = roles.propose_assignment(fx._proposal_command(role, membership, actor=member if self_proposed else founder))
    assignment = models.ContextualRoleAssignment.objects.get(assignment_uuid=proposed.assignment_uuid)
    if not self_proposed:
        OfficeAppointmentConsentService(authority=authority, clock=support.database_now).record_office_consent(
            fx._consent_command(membership, assignment))
    return assignment
