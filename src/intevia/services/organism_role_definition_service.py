"""SR: the role-definition record and its one writer.

Design: SR_COMMISSION_OTHER_OFFICES_v0_7.md (adopted 15 October 2026) sections C, D.1,
D.2, D.3 and G; the Lead Designer's plan v1.1 row SR-C1 (US015-B-35, 26 October 2026).
Chunk C1 ships this module with ``_write_definition_version`` only: nothing else writes
``core_organismroledefinition`` or ``core_organismroledefinitionprovision``. Founding
(``organism_formation_service.py``) records version 1 of a new LO's COORDINATOR definition
through it. The definition command (DEFINE / AMEND / RETIRE through the intake,
``OrganismRoleDefinitionService.record_role_definition``) lands in chunk C3 on this writer;
the ``_OrganismCommandService`` subclass is declared here so the module's shape is fixed,
with the command itself absent until then.

What the writer does and does not do. It writes one version row and its provision rows in
the caller's transaction and returns the row; the database guards installed by migration
0051 are the authority on what a lawful row is (``sr_00_definition_insert`` compares the
decision the caller passes against the row, column by column; ``sr_30_definition_citations``
checks the citations at commit). The writer does not qualify authority, does not open a
transaction, does not write a receipt and does not catch the guards' refusals: a refusal
propagates as the database's ``IntegrityError`` for the caller's class-B translation at its
own transaction exit (v0.7 section G).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from django.db import connections

from core.models import (
    Identity,
    LivingOrganism,
    OrganismRoleDefinition,
    OrganismRoleDefinitionProvision,
)
from src.intevia.services.organism_command_support import (
    _OrganismCommandService,
    _validate_timestamp,
)


class S015RoleDefinitionError(Exception):
    """Base class for this module's definition errors."""


class S015RoleDefinitionNotFound(S015RoleDefinitionError):
    """The organism, the actor or the superseded version was not found."""


class S015RoleDefinitionAuthorityUnavailable(S015RoleDefinitionError):
    """Authority could not be consulted (chunk C3's command)."""


class S015RoleDefinitionMalformed(S015RoleDefinitionError):
    """A writer argument is not of the shape the writer requires; nothing is written."""


_RELATIONSHIPS = frozenset(
    member.value for member in OrganismRoleDefinitionProvision.Relationship
)
_KINDS = frozenset(member.value for member in OrganismRoleDefinition.Kind)
_APPOINTING = frozenset(member.value for member in OrganismRoleDefinition.AppointingAuthority)
_BASES = frozenset(member.value for member in OrganismRoleDefinition.NominationBasis)
_POLICIES = frozenset(member.value for member in OrganismRoleDefinition.NominationPolicy)


@dataclass(frozen=True, slots=True)
class DefinitionProvision:
    """One constitutional citation of a definition version (section D.2).

    relationship is one of OrganismRoleDefinitionProvision.Relationship's
    values; provision_reference is the LIBRARY version pointer and clause
    locator (HD-C9); provision_version its version (HD-EG-2).
    """
    relationship: str
    provision_reference: str
    provision_version: str


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise S015RoleDefinitionMalformed(f"{name} must be a non-empty string")
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _write_definition_version(
    *,
    database_alias: str,
    organism: LivingOrganism,
    code: str,
    kind: str,
    appointing_authority: str,
    appointing_body_reference: str | None,
    alternative_route_reference: str | None,
    nomination_basis: str,
    nomination_policy: str,
    nomination_dependency: str | None,
    participation_required: bool,
    ex_officio_seat_reference: str | None,
    supersedes: OrganismRoleDefinition | None,
    enterer: Identity,
    authority_decision_reference: str,
    occurred_at: datetime,
    effective_at: datetime,
    received_at: datetime,
    provisions: tuple[DefinitionProvision, ...] = (),
    role_uuid: UUID | None = None,
) -> OrganismRoleDefinition:
    """Write one LO-scoped definition version and its provision rows; return the row.

    The one writer of the definition tables (v0.7 section C). Called inside
    the caller's open transaction on database_alias (a top-level
    ``authority_atomic`` for a command; founding's batch), after the caller
    has qualified authority: authority_decision_reference is the QUALIFIED
    decision the row cites - the founding decision (``FOUND_LIVING_ORGANISM``)
    for version 1 of a new LO's COORDINATOR definition, or the definition act's
    decision (``RECORD_ROLE_DEFINITION``, chunk C3) - and the insert guard
    requires that decision to name this organism, actor and time.

    Writes, in order: the version row (``definition_version`` = the (lo, code)
    maximum + 1 as the writer reads it; the guard recomputes it), then one
    provision row per citation. ``recorded_at`` on each row is left to the
    database. On AMEND (supersedes given) the caller has already deactivated
    the prior version in the same transaction; the writer does not touch it.
    The retirement (section D.3 (a)) is not this function: it is one
    ``QuerySet.update`` on the active row, by the command in chunk C3.

    Raises S015RoleDefinitionMalformed before any write when an argument is
    outside its domain or a required pair is inconsistent (the guard would
    refuse the same, after the write); the caller's atomic block decides what
    that leaves. Raises nothing of its own for the guards' refusals: a
    database refusal propagates as IntegrityError.
    """
    if type(database_alias) is not str or not database_alias:
        raise S015RoleDefinitionMalformed("database_alias is required")
    connection = connections[database_alias]
    if not connection.in_atomic_block:
        raise S015RoleDefinitionMalformed("the definition writer runs inside the caller's transaction")
    if not isinstance(organism, LivingOrganism) or organism.pk is None:
        raise S015RoleDefinitionNotFound("organism is required")
    if not isinstance(enterer, Identity) or enterer.pk is None:
        raise S015RoleDefinitionNotFound("enterer is required")
    code = _text(code, "code")
    if kind not in _KINDS:
        raise S015RoleDefinitionMalformed("kind is outside its domain")
    if appointing_authority not in _APPOINTING:
        raise S015RoleDefinitionMalformed("appointing_authority is outside its domain")
    if nomination_basis not in _BASES:
        raise S015RoleDefinitionMalformed("nomination_basis is outside its domain")
    if nomination_policy not in _POLICIES:
        raise S015RoleDefinitionMalformed("nomination_policy is outside its domain")
    if type(participation_required) is not bool:
        raise S015RoleDefinitionMalformed("participation_required must be a bool")
    appointing_body_reference = _optional_text(appointing_body_reference, "appointing_body_reference")
    alternative_route_reference = _optional_text(alternative_route_reference, "alternative_route_reference")
    nomination_dependency = _optional_text(nomination_dependency, "nomination_dependency")
    ex_officio_seat_reference = _optional_text(ex_officio_seat_reference, "ex_officio_seat_reference")
    if (appointing_authority == "NAMED_BODY") != (appointing_body_reference is not None):
        raise S015RoleDefinitionMalformed("appointing_body_reference is required iff NAMED_BODY")
    if (nomination_policy == "UNSUPPORTED") != (nomination_dependency is not None):
        raise S015RoleDefinitionMalformed("nomination_dependency is required iff UNSUPPORTED")
    if nomination_policy == "UNSUPPORTED" and nomination_basis != "CONSTITUTION":
        raise S015RoleDefinitionMalformed("UNSUPPORTED requires a constitutional nomination basis")
    if (kind == "CUSTOM") != (nomination_basis == "CUSTOM"):
        raise S015RoleDefinitionMalformed("nomination_basis is CUSTOM iff kind is CUSTOM")
    if (code == "COORDINATOR") != (kind == "COORDINATOR"):
        raise S015RoleDefinitionMalformed("kind is COORDINATOR iff code is COORDINATOR")
    authority_decision_reference = _text(authority_decision_reference, "authority_decision_reference")
    occurred_at = _validate_timestamp(occurred_at, "occurred_at")
    effective_at = _validate_timestamp(effective_at, "effective_at")
    received_at = _validate_timestamp(received_at, "received_at")
    if supersedes is not None and (
        not isinstance(supersedes, OrganismRoleDefinition) or supersedes.pk is None
    ):
        raise S015RoleDefinitionNotFound("supersedes must be a recorded definition version")
    if role_uuid is not None and type(role_uuid) is not UUID:
        raise S015RoleDefinitionMalformed("role_uuid must be a UUID")
    seen: set[str] = set()
    for provision in provisions:
        if not isinstance(provision, DefinitionProvision):
            raise S015RoleDefinitionMalformed("provisions must be DefinitionProvision values")
        if provision.relationship not in _RELATIONSHIPS:
            raise S015RoleDefinitionMalformed("provision relationship is outside its domain")
        if provision.relationship in seen:
            raise S015RoleDefinitionMalformed("one citation per relationship per version")
        seen.add(provision.relationship)
        _text(provision.provision_reference, "provision_reference")
        _text(provision.provision_version, "provision_version")

    latest = (
        OrganismRoleDefinition.objects.using(database_alias)
        .filter(living_organism=organism, code=code)
        .order_by("-definition_version")
        .values_list("definition_version", flat=True)
        .first()
    )
    row = OrganismRoleDefinition(
        role_uuid=uuid4() if role_uuid is None else role_uuid,
        living_organism=organism,
        code=code,
        scope=OrganismRoleDefinition.Scope.LIVING_ORGANISM,
        definition_version=(latest or 0) + 1,
        active=True,
        kind=kind,
        appointing_authority=appointing_authority,
        appointing_body_reference=appointing_body_reference,
        alternative_route_reference=alternative_route_reference,
        nomination_basis=nomination_basis,
        nomination_policy=nomination_policy,
        nomination_dependency=nomination_dependency,
        participation_required=participation_required,
        ex_officio_seat_reference=ex_officio_seat_reference,
        supersedes=supersedes,
        enterer=enterer,
        enterer_access_epoch=enterer.access_epoch,
        authority_decision_reference=authority_decision_reference,
        occurred_at=occurred_at,
        effective_at=effective_at,
        received_at=received_at,
        recorded_at=None,
    )
    row.save(using=database_alias)
    for provision in provisions:
        OrganismRoleDefinitionProvision(
            provision_uuid=uuid4(),
            definition=row,
            relationship=provision.relationship,
            provision_reference=provision.provision_reference,
            provision_version=provision.provision_version,
            recorded_at=None,
        ).save(using=database_alias)
    return row


class OrganismRoleDefinitionService(_OrganismCommandService):
    """The definition command's service (DEFINE / AMEND / RETIRE), chunk C3.

    Declared in C1 so that the module's shape - one service, one writer - is
    fixed; ``record_role_definition`` does not exist yet and the
    ``RECORD_ROLE_DEFINITION`` action is not in ``OrganismAction`` until C3.
    """

    _not_found = S015RoleDefinitionNotFound
    _authority_unavailable = S015RoleDefinitionAuthorityUnavailable


__all__ = [
    "DefinitionProvision",
    "OrganismRoleDefinitionService",
    "S015RoleDefinitionAuthorityUnavailable",
    "S015RoleDefinitionError",
    "S015RoleDefinitionMalformed",
    "S015RoleDefinitionNotFound",
    "_write_definition_version",
]
