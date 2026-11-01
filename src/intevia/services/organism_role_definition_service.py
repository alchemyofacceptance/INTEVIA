"""SR: the role-definition record and its one writer.

Design: SR_COMMISSION_OTHER_OFFICES_v0_7.md (adopted 15 October 2026) sections C, D.1,
D.2, D.3 and G; the Lead Designer's plan v1.1 row SR-C1 (US015-B-35, 26 October 2026).
C3 exposes DEFINE / AMEND / RETIRE through the durable intake on C1's version writer.
Founding continues to call that writer directly under its founding decision.
Retirement ends a custom role's use when vacant (v0.7 l.45, R-2, as restricted by the HG on 31 Oct 2026); board offices are permanent.
HG 31 Oct 2026: "Lineage is King". Retirement reads no law-in-force (LAW held).
The command appends versions and citations, or records a one-way retirement; it appoints nobody.
`latest` resolves on (LO, code) across scopes, deliberately matching 0051's version namespace.
A prior version is separately LO/scope-bound; future Circle-scoped writers must preserve or
explicitly revise that shared namespace rather than inherit an accidental scope assumption.
COORDINATOR DEFINE/AMEND/RETIRE are refused before the decision (HG 31 Oct 2026, F-20 A; v0.7 Bound l.25; plan SR-C1 l.209).

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

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID, uuid4

from django.db import DatabaseError, connections

from core.models import (
    Identity,
    LivingOrganism,
    OrganismRoleDefinition,
    OrganismRoleDefinitionProvision,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import S015AuthorityDenied
from src.intevia.services.organism_command_support import (
    ReplayViewer, S015CommandReceiptMalformed, S015CommandReplayMismatch, _OrganismCommandService,
    _canonical_key, _canonical_uuid, _validate_timestamp,
    _lock_actor, _lock_actor_membership_context, _lock_registered_organism,
    _read_actor_active_membership, authority_atomic, acquire_organism_command_mutex,
    authority_target_fingerprint, canonical_json_bytes, canonical_timestamp,
)

# SUPPORT classification is independent of the transaction refusal classes.
SUPPORT_INTERIM_MESSAGE = "Something went wrong and INTEVIA could not complete this action. No changes have been made to this organisation's offices. This has been recorded with the details needed to investigate; please try again later or contact INTEVIA Support."
DEFINITION_REFUSAL_MESSAGES = {
    'SR_DEFINITION_KIND_FIXED': ('You cannot change this office from a board office to a custom role, or from a custom role to a board office. An office keeps the kind it was recorded with, and its history is kept. No changes have been made to the office.', 'B'),
    'SR_DESIGNATION_INCOMPLETE': ('This board office could not be recorded. A board office must cite the provision of your constitution that creates it, and which version; this designation does not. No office has been created or changed. Add the citation and submit again — or, if no provision creates this office, record it as a custom role.', 'A'),
    'SR_RETIREMENT_HOLDER_REMAINS': ("This office still has a holder. End or reassign the appointment through its own route first; retiring the definition does not end anyone's office. Nothing has changed.", 'A'),
    'SR_ROOT_OFFICE_RESERVED': ("This is a reserved INTEVIA root office. It is not defined, changed, retired or filled through this route; its route is the platform's root founding. Nothing has changed.", 'B'),
    'SR_COORDINATOR_DEFINITION_RESERVED': ('You cannot create, change or retire the Coordinator office here. This office is established when the Living Organism is founded. No changes have been made to the office.', 'B'),
    'SR_DEFINITION_EXISTS': ('This office could not be created: this organisation already has an office with this identity. No office has been created or changed. To change how the existing office is defined, amend it.', 'A'),
    'SR_DEFINITION_RETIRED': ('This office has been retired, so it cannot be changed, retired again, or re-established here. No changes have been made. Its definition and history remain on record.', 'B'),
    'SR_SUPERSEDED_DEFINITION': ("This office's definition has been updated since the version you were working from. No changes have been made. Open the current definition and make the change there.", 'A'),
    'SR_GOVERNANCE_RELATIONSHIP_UNCITED': ('This office could not be recorded. Each relationship between this office and your GOVERNANCE Circle — who elects or appoints to it, whether holding it requires GOVERNANCE participation, and any seat it carries — must cite the provision of your constitution that provides it, and which version; this designation leaves one uncited. No office has been created or changed. Add the citation, or remove the relationship, and submit again.', 'A'),
    'SR_BOARD_OFFICE_PERMANENT': ("Board offices cannot be retired. A board office is part of this organisation's governing structure, and its history is kept. If no one holds it, it simply remains vacant. No changes have been made to the office.", 'B'),
}
DEFINITION_AUTHORITY_MESSAGES = {
    'C_DEFINITION_KIND_FIXED': ('You cannot change this office from a board office to a custom role, or from a custom role to a board office. An office keeps the kind it was recorded with, and its history is kept. No changes have been made to the office.', 'B'),
    'E_CUSTOM_ROLE_NOT_ENTITLED': ("This custom role could not be recorded. Custom roles are a subscription capability, and this organisation does not have an active subscription or complimentary access at this time. No custom role has been created or changed; the request and this refusal are on record. Your constitution's board offices can be recorded free. Existing custom-role appointments continue; new ones cannot start.", 'A'),
    'E_NOMINATION_POLICY_NOT_ENTITLED': ('The nomination rule for this office could not be recorded. Member nominations or Coordinator nominations can be chosen only under an entitlement that permits it, and this organisation does not have one at this time. No office has been created or changed; the request and this refusal are on record. Without the entitlement, the standard rule applies: members may put themselves forward and effective Coordinators may nominate others. Choose that rule, or arrange the entitlement first.', 'A'),
    'C_RESERVED_ROOT_OFFICE': ("This is a reserved INTEVIA root office. It is not defined, changed, retired or filled through this route; its route is the platform's root founding. Nothing has changed.", 'B'),
    'C_COORDINATOR_DEFINITION_RESERVED': ('You cannot create, change or retire the Coordinator office here. This office is established when the Living Organism is founded. No changes have been made to the office.', 'B'),
    'C_BOARD_OFFICE_PERMANENT': ("Board offices cannot be retired. A board office is part of this organisation's governing structure, and its history is kept. If no one holds it, it simply remains vacant. No changes have been made to the office.", 'B'),
    'C_ROUTE_IS_CQ': ('This change to an office definition could not be made by the Coordinators alone: this organisation decides such matters through its Collective Questioning process. No office has been created or changed; the request and this refusal are on record. Raise it through that process.', 'A'),
    'E_NOT_EFFECTIVE_COORDINATOR': ("You cannot define, change or retire this organisation's offices: this action is for its effective Coordinators. No changes have been made; the request and this refusal are on record.", 'B'),
    'DENIED': ("You cannot define, change or retire this organisation's offices: this action is for its effective Coordinators. No changes have been made; the request and this refusal are on record.", 'B'),
}
DEFINITION_STATE_REFUSALS = frozenset(['E_IDENTITY_INACTIVE', 'C_CONFIGURATION_NOT_ESTABLISHED', 'C_LO_NO_CAPACITY', 'C_LO_PAUSED', 'C_LO_CLOSED', 'SBG_LO_NOT_ACTIVE', 'SBG_SEAT_ROUTE_UNAVAILABLE'])
ORGANISM_NOT_FOUND_MESSAGE = 'The organisation could not be found for this office operation. No organisation matches the supplied reference. No office has been changed. Open the organisation you intended to use and start the operation again.'
DEFINITION_NOT_FOUND_MESSAGE = 'The office definition could not be found for this operation. The supplied version does not identify an office definition in this organisation. No office has been changed. Open the office from the intended organisation and start the operation again.'
DEFINITION_STATE_MESSAGES = {
    'E_IDENTITY_INACTIVE': ('Creating, changing or retiring this office definition was refused. INTEVIA could not confirm that your personal access record and current sign-in details allow you to act now. No office definition has been changed; your request and this refusal are on record. Sign in again before trying once more. If your access is still not active, you cannot make this change and there is no recovery action offered here.', 'B'),
    'C_CONFIGURATION_NOT_ESTABLISHED': ("Creating, changing or retiring this office definition was refused. This organisation's governance settings have not been established. No office definition has been changed; your request and this refusal are on record. Try again after the required governance settings have been established. An effective Coordinator of this organisation can establish those settings.", 'A'),
    'C_LO_NO_CAPACITY': ("Creating, changing or retiring this office definition was refused. The organisation's history does not show that this action was allowed at the time requested: its founding may not yet be recorded for that time, it may already have closed, or its stage may not permit this action. This refusal does not identify which applies. No office definition has been changed; your request and this refusal are on record. You cannot make this change until the organisation's recorded status permits it; repeating the request will not change that status.", 'B'),
    'C_LO_PAUSED': ('Creating, changing or retiring this office definition was refused. This organisation is paused. No office definition has been changed; your request and this refusal are on record. Wait until the organisation is active before requesting this office-definition change again.', 'B'),
    'C_LO_CLOSED': ('Creating, changing or retiring this office definition was refused. This organisation is closed. No office definition has been changed; your request and this refusal are on record. You cannot make this office-definition change in a closed organisation. Do not repeat it here.', 'B'),
    'SBG_LO_NOT_ACTIVE': ('Creating, changing or retiring this office definition was refused. This organisation is not active. No office definition has been changed; your request and this refusal are on record. You cannot make this office-definition change until the organisation is active.', 'B'),
    'SBG_SEAT_ROUTE_UNAVAILABLE': ("Creating, changing or retiring this office definition was refused. The requested way of giving someone a GOVERNANCE seat does not match the organisation's or Circle's stage of development. Founding seats are handled before Birthing; later appointments must follow the process allowed at that stage. No office definition has been changed; your request and this refusal are on record. You cannot use the requested seating process at this stage; repeating the request will not make it available.", 'B'),
}


def definition_support_details(error):
    """Return SUPPORT class and display text; create no ticket or audit record.

    Shared exceptions retain their type and original diagnostic message. R-17's
    replay wording belongs to the shared handler (FW-8); it is classified A here.
    A future SERVICE boundary consumes these attributes and owns ticket creation.
    """
    if isinstance(error, S015AuthorityDenied):
        code = getattr(error.disclosed_refusal_code, "value", None)
        if error.authority_decision_reference is None:
            return "C", SUPPORT_INTERIM_MESSAGE
        if code in DEFINITION_AUTHORITY_MESSAGES:
            message, support_class = DEFINITION_AUTHORITY_MESSAGES[code]
            return support_class, message
        if code in DEFINITION_STATE_REFUSALS:
            message, category = DEFINITION_STATE_MESSAGES[code]
            return category, message
        return "C", SUPPORT_INTERIM_MESSAGE
    if isinstance(error, S015CommandReplayMismatch):
        return "A", str(error)
    support_class = getattr(error, "support_class", "C")
    return support_class, str(error) if support_class != "C" else SUPPORT_INTERIM_MESSAGE


class S015RoleDefinitionError(Exception):
    """Definition error; SUPPORT C requires the future support boundary."""
    support_class = "C"


class S015RoleDefinitionNotFound(S015RoleDefinitionError):
    """SUPPORT A for command context; internal writer lookup faults are C."""
    support_class = "A"

    def __init__(self, message, *, cause=None):
        self.cause = cause
        self.support_class = "A" if cause is not None else "C"
        super().__init__(message)


class S015RoleDefinitionAuthorityUnavailable(S015RoleDefinitionError):
    """Authority unavailable; SUPPORT C."""
    support_class = "C"


class S015RoleDefinitionMalformed(S015RoleDefinitionError):
    """Malformed command or writer content; SUPPORT C; intake may remain."""
    support_class = "C"


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


_ACTION = OrganismAction.RECORD_ROLE_DEFINITION
_TARGET_SCHEMA = "intevia.s015.role-definition-target.v1"


class S015RoleDefinitionRefused(S015RoleDefinitionError):
    """Business refusal; reserved/permanent offices refuse before the decision.

    Other class-C refusals follow qualification. SUPPORT A/B is separate from
    transaction class C; governed writes roll back and durable intake may remain.
    """
    def __init__(self, code, message):
        self.code = code
        self.support_class = DEFINITION_REFUSAL_MESSAGES.get(code, (None, "C"))[1]
        super().__init__(message)


class S015RoleDefinitionGuardRefused(S015RoleDefinitionError):
    """Database refusal after rollback; preserve primary, classify by service twin."""
    def __init__(self, primary):
        self.primary = primary
        text, self.support_class = definition_guard_message(primary)
        super().__init__(text)


def definition_guard_message(primary):
    """Translate known business twins only; unmatched guards remain SUPPORT C."""
    twins = {
        "SR definition refused: office basis citation missing": "SR_DESIGNATION_INCOMPLETE",
        "SR definition refused: this office still has a holder or a pending nomination": "SR_RETIREMENT_HOLDER_REMAINS",
        "SR definition refused: reserved root office": "SR_ROOT_OFFICE_RESERVED",
    }
    if isinstance(primary, str) and any(primary == f"SR definition refused: {kind} ({rel})"
            for kind in ("governance relationship citation missing", "citation without its relationship")
            for rel in _RELATIONSHIPS):
        twins[primary] = "SR_GOVERNANCE_RELATIONSHIP_UNCITED"
    if primary == "SR definition refused: custom role entitlement absent at receipt":
        # A class-B rollback retains no refusal decision: do not claim one exists.
        text, category = DEFINITION_AUTHORITY_MESSAGES["E_CUSTOM_ROLE_NOT_ENTITLED"]
        text = text.replace("; the request and this refusal are on record.", ".")
        return "At the moment of recording, " + text, category
    if primary in twins:
        text, category = DEFINITION_REFUSAL_MESSAGES[twins[primary]]
        return "At the moment of recording, " + text, category
    return SUPPORT_INTERIM_MESSAGE, "C"


@dataclass(frozen=True, slots=True)
class DefinitionContent:
    """The complete immutable content of one version; citations sorted in fingerprints."""
    code: str
    kind: str = "BOARD_OFFICE"
    appointing_authority: str = "LO_COORDINATOR_BODY"
    appointing_body_reference: str | None = None
    alternative_route_reference: str | None = None
    nomination_basis: str = "SILENT"
    nomination_policy: str = "C"
    nomination_dependency: str | None = None
    participation_required: bool = False
    ex_officio_seat_reference: str | None = None
    provisions: tuple[DefinitionProvision, ...] = ()


@dataclass(frozen=True, slots=True)
class RecordRoleDefinitionCommand:
    """A definition act. The intake supplies received_at; clients supply no receipt time.

    DEFINE has content only; AMEND has content and the exact prior UUID; RETIRE
    has that UUID and a nonempty reason only. AMEND cannot change a code family.
    """
    credential: object
    living_organism_uuid: UUID
    event_action: str
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    content: DefinitionContent | None = None
    role_definition_uuid: UUID | None = None
    retirement_reason: str | None = None


def _content_dict(content):
    """Validate representation only, before intake; citation sufficiency is after authority."""
    if type(content) is not DefinitionContent:
        raise S015RoleDefinitionMalformed("content must be DefinitionContent")
    if len(_text(content.code, "code")) > 48:
        raise S015RoleDefinitionMalformed("code exceeds 48 characters")
    for value, domain in ((content.kind, _KINDS), (content.appointing_authority, _APPOINTING),
                          (content.nomination_basis, _BASES), (content.nomination_policy, _POLICIES)):
        if type(value) is not str or value not in domain:
            raise S015RoleDefinitionMalformed("definition value outside its domain")
    if type(content.participation_required) is not bool:
        raise S015RoleDefinitionMalformed("participation_required must be bool")
    for name in ("appointing_body_reference", "alternative_route_reference", "nomination_dependency", "ex_officio_seat_reference"):
        _optional_text(getattr(content, name), name)
    if (content.appointing_authority == "NAMED_BODY") != (content.appointing_body_reference is not None):
        raise S015RoleDefinitionMalformed("appointing body is required iff NAMED_BODY")
    if (content.nomination_policy == "UNSUPPORTED") != (content.nomination_dependency is not None):
        raise S015RoleDefinitionMalformed("UNSUPPORTED requires its dependency and only it")
    if content.nomination_policy == "UNSUPPORTED" and content.nomination_basis != "CONSTITUTION":
        raise S015RoleDefinitionMalformed("UNSUPPORTED requires CONSTITUTION")
    if (content.kind == "CUSTOM") != (content.nomination_basis == "CUSTOM"):
        raise S015RoleDefinitionMalformed("CUSTOM kind and basis must agree")
    if (content.code == "COORDINATOR") != (content.kind == "COORDINATOR"):
        raise S015RoleDefinitionMalformed("COORDINATOR kind and code must agree")
    if type(content.provisions) is not tuple:
        raise S015RoleDefinitionMalformed("provisions must be a tuple")
    seen = set()
    for p in content.provisions:
        if type(p) is not DefinitionProvision or p.relationship not in _RELATIONSHIPS or p.relationship in seen:
            raise S015RoleDefinitionMalformed("one typed citation per declared relationship")
        seen.add(p.relationship)
        _text(p.provision_reference, "provision_reference")
        _text(p.provision_version, "provision_version")
    result = asdict(content)
    result["provisions"] = sorted(result["provisions"], key=lambda p: p["relationship"])
    return result


def definition_intake_shape(command):
    """Pure canonical projection: all requested content, coordinates and reason are bound."""
    from src.intevia.services.command_intake import IntakeShape
    if type(command) is not RecordRoleDefinitionCommand:
        raise TypeError("RECORD_ROLE_DEFINITION requires its exact command type")
    action = command.event_action
    if action not in {"DEFINE", "AMEND", "RETIRE"}:
        raise S015RoleDefinitionMalformed("event_action must be DEFINE, AMEND or RETIRE")
    lo = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
    role = command.role_definition_uuid
    if action == "DEFINE":
        if role is not None:
            raise S015RoleDefinitionMalformed("DEFINE has no prior version")
    else:
        _canonical_uuid(role, "role_definition_uuid")
    if action == "RETIRE":
        if command.content is not None:
            raise S015RoleDefinitionMalformed("RETIRE accepts no replacement content")
        reason = _text(command.retirement_reason, "retirement_reason")
        content = None
    else:
        if command.retirement_reason is not None:
            raise S015RoleDefinitionMalformed("only RETIRE carries a reason")
        reason = None
        content = _content_dict(command.content)
    occurred = _validate_timestamp(command.occurred_at, "occurred_at")
    effective = _validate_timestamp(command.effective_at, "effective_at")
    return IntakeShape(
        idempotency_key=_canonical_key(command.idempotency_key, "idempotency_key", 120),
        request_reference=_canonical_key(command.request_reference, "request_reference", 128),
        target={"living_organism_uuid": str(lo), "role_definition_uuid": None if role is None else str(role)},
        command={"event_action": action, "content": content, "retirement_reason": reason,
                 "occurred_at": canonical_timestamp(occurred), "effective_at": canonical_timestamp(effective)},
        omitted=(), target_organism_id=None,
    )


def custom_role_entitled(organism_uuid, received_at, database_alias):
    """Read CUSTOM_ROLES once at the durable receipt coordinate; failure is not denial."""
    # Same installed predicate as C1 sr_00; no dependency on an uncarried Python API.
    with connections[database_alias].cursor() as cursor:
        cursor.execute("SELECT COALESCE(public.s015_entitlement_effective(%s, 'CUSTOM_ROLES', %s), FALSE)",
                       [organism_uuid, received_at])
        return cursor.fetchone()[0]  # psycopg: bool; SQL/API failures propagate.


def definition_target_valid(target, organism_uuid):
    """Validate the command's closed target shape; called independently by the provider."""
    if type(target) is not dict:
        return False
    keys = {"action", "schema", "event_action", "living_organism_id", "role_uuid",
            "definition_version", "supersedes_role_uuid", "retirement_reason"} | set(DefinitionContent.__dataclass_fields__)
    if set(target) != keys or target.get("action") != _ACTION.value or target.get("schema") != _TARGET_SCHEMA:
        return False
    if target.get("living_organism_id") != str(organism_uuid) or target.get("event_action") not in {"DEFINE", "AMEND", "RETIRE"}:
        return False
    if type(target.get("definition_version")) is not int or target["definition_version"] < 1:
        return False
    try:
        for name in ("role_uuid", "supersedes_role_uuid"):
            if target[name] is not None and str(UUID(target[name])) != target[name]:
                return False
        if type(target["code"]) is not str or not target["code"] or type(target["kind"]) is not str or not target["kind"]:
            return False
        if target["event_action"] != "RETIRE":
            fields = {k: target[k] for k in DefinitionContent.__dataclass_fields__}
            fields["provisions"] = tuple(DefinitionProvision(**p) for p in fields["provisions"])
            _content_dict(DefinitionContent(**fields))
    except (ValueError, TypeError, KeyError, S015RoleDefinitionError):
        return False
    if target["event_action"] == "DEFINE":
        return target["role_uuid"] is None and target["supersedes_role_uuid"] is None and target["retirement_reason"] is None
    if target["event_action"] == "AMEND":
        return target["role_uuid"] is not None and target["supersedes_role_uuid"] == target["role_uuid"] and target["retirement_reason"] is None
    return target["role_uuid"] is not None and target["supersedes_role_uuid"] is None and type(target["retirement_reason"]) is str and bool(target["retirement_reason"].strip())


def _check_citations(content):
    """Class C, after qualification; sr_30 independently enforces the same at commit."""
    required = {
        name for name, needed in (
            ("OFFICE_BASIS", content.kind == "BOARD_OFFICE"),
            ("APPOINTING_AUTHORITY", content.appointing_authority == "NAMED_BODY"),
            ("ALTERNATIVE_ROUTE", content.alternative_route_reference is not None),
            ("NOMINATION_ROUTE", content.nomination_basis == "CONSTITUTION"),
            ("PARTICIPATION_REQUIRED", content.participation_required),
            ("EX_OFFICIO_SEAT", content.ex_officio_seat_reference is not None),
        ) if needed
    }
    present = {p.relationship for p in content.provisions}
    if "OFFICE_BASIS" in required - present:
        raise S015RoleDefinitionRefused('SR_DESIGNATION_INCOMPLETE', DEFINITION_REFUSAL_MESSAGES['SR_DESIGNATION_INCOMPLETE'][0])
    if present != required:
        raise S015RoleDefinitionRefused('SR_GOVERNANCE_RELATIONSHIP_UNCITED', DEFINITION_REFUSAL_MESSAGES['SR_GOVERNANCE_RELATIONSHIP_UNCITED'][0])


def _has_unended_assignment(alias, organism, code, *, at=None):
    """Class-C pre-check for the better message, using the family-wide state fold.

    This clock sample precedes sr_10's own sample. The database retirement guard
    remains authoritative for the committed act; this check cannot replace it.
    """
    with connections[alias].cursor() as cursor:
        if at is None:
            cursor.execute("SELECT clock_timestamp()")
            at = cursor.fetchone()[0]  # psycopg: aware datetime, one sample, matching sr_10
        cursor.execute("""SELECT EXISTS (
            SELECT 1 FROM core_contextualroleassignment a
            JOIN core_organismroledefinition d ON d.id = a.role_definition_id
            WHERE d.living_organism_id = %s AND d.code = %s
              AND public.s015_fold_effective_state('core_contextualroleassignment', a.id,
                  %s, %s) IS DISTINCT FROM 'ENDED')""", [organism.pk, code, at, at])
        return cursor.fetchone()[0]  # psycopg: bool


@dataclass(frozen=True, slots=True)
class RoleDefinitionResult:
    """Receipt result: identity remains replayable; detailed authority is audience checked."""
    role_definition_uuid: UUID
    event_action: str
    definition_version: int
    authority_reference: str
    authority_decision_reference: str
    lineage_reference: str

    def receipt_payload(self):
        result = asdict(self)
        result["role_definition_uuid"] = str(self.role_definition_uuid)
        return result

    @classmethod
    def from_payload(cls, payload):
        try:
            if type(payload) is not dict or set(payload) != set(cls.__dataclass_fields__):
                raise ValueError("receipt fields differ")
            if payload["event_action"] not in {"DEFINE", "AMEND", "RETIRE"} or type(payload["definition_version"]) is not int or payload["definition_version"] < 1:
                raise ValueError("invalid receipt act or version")
            for key in ("authority_reference", "authority_decision_reference", "lineage_reference"):
                if type(payload[key]) is not str or not payload[key]:
                    raise ValueError("invalid receipt reference")
            if not re.fullmatch(r"s015d1:[0-9a-f]{64}", payload["authority_decision_reference"]):
                raise ValueError("invalid decision reference")
            if not re.fullmatch(r"s015r1:[0-9a-f]{64}", payload["lineage_reference"]):
                raise ValueError("invalid lineage reference")
            return cls(**{**payload, "role_definition_uuid": UUID(payload["role_definition_uuid"])})
        except (ValueError, TypeError, KeyError) as exc:
            raise S015CommandReceiptMalformed() from exc

    @classmethod
    def replay_field_audiences(cls, result):
        if type(result) is not cls:
            raise TypeError("wrong definition result")
        return {"authority_reference": "PROCESSING", "authority_decision_reference": "PROCESSING"}


@dataclass(frozen=True, slots=True)
class BoardOfficeVacancy:
    """Recorded board definition and cited basis, with the commissioned holder fold."""
    role_identity: UUID
    code: str
    office_basis_reference: str | None
    office_basis_version: str | None
    filled: bool


def board_office_vacancies(organism_uuid, database_alias="default"):
    """Read active board definitions and citations for one LO; write and decide nothing.

    Foundation for CARE's later observation: "an office your constitution provides
    for is unfilled". CARE mechanics and law-in-force interpretation are FW-9.
    Filled means an assignment under any version of the code folds not ENDED,
    exactly as _has_unended_assignment (including a pending nomination). No own
    transaction or locks; one clock cut is shared across every office fold. Reads
    still require a caller-supplied repeatable snapshot for concurrent consistency.
    Missing citations are returned as None, never invented.
    """
    try:
        organism = LivingOrganism.objects.using(database_alias).get(organism_id=organism_uuid)
    except LivingOrganism.DoesNotExist as exc:
        raise S015RoleDefinitionNotFound(ORGANISM_NOT_FOUND_MESSAGE, cause="living organism not found") from exc
    with connections[database_alias].cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        at = cursor.fetchone()[0]  # psycopg: aware datetime, shared by every office fold
    rows = OrganismRoleDefinition.objects.using(database_alias).filter(
        living_organism=organism, scope="LIVING_ORGANISM", kind="BOARD_OFFICE", active=True,
    ).prefetch_related("provisions").order_by("code", "pk")
    result = []
    for row in rows:
        basis = next((p for p in row.provisions.all() if p.relationship == "OFFICE_BASIS"), None)
        result.append(BoardOfficeVacancy(row.role_uuid, row.code,
            None if basis is None else basis.provision_reference,
            None if basis is None else basis.provision_version,
            _has_unended_assignment(database_alias, organism, row.code, at=at)))
    return tuple(result)


def _lineage(row, action, decision_reference):
    return "s015r1:" + hashlib.sha256(b"INTEVIA:S015:ROLE_DEFINITION_RESULT:v1\x00" + canonical_json_bytes({
        "role_uuid": str(row.role_uuid), "definition_version": row.definition_version,
        "event_action": action, "decision_reference": decision_reference,
    })).hexdigest()


class OrganismRoleDefinitionService(_OrganismCommandService):
    """One governed definition command on C1's writer. No office or participation writer.

    Intake commits separately. A provider refusal retains its REFUSED decision;
    class B/C/D failures roll back the in-flight decision, content and receipt.
    A successful return follows the real transaction exit, including deferred guards.
    """
    _action = _ACTION
    _not_found = S015RoleDefinitionNotFound
    _authority_unavailable = S015RoleDefinitionAuthorityUnavailable

    def _replay_result(self, payload):
        return RoleDefinitionResult.from_payload(payload)

    def _verify_replay_result(self, result):
        row = OrganismRoleDefinition.objects.using(self._alias).filter(role_uuid=result.role_definition_uuid).first()
        if row is None:
            raise S015CommandReceiptMalformed() from ValueError("definition missing")
        reference = row.retired_by_decision_reference if result.event_action == "RETIRE" else row.authority_decision_reference
        if (reference != result.authority_decision_reference or row.definition_version != result.definition_version
            or (result.event_action != "RETIRE" and (result.event_action == "DEFINE") != (row.supersedes_id is None))
            or _lineage(row, result.event_action, reference) != result.lineage_reference):
            raise S015CommandReceiptMalformed() from ValueError("receipt disagrees with definition")

    def record_role_definition(self, command):
        """Run once and attach the SUPPORT hook; preserve exception type and cause."""
        try:
            return self._record_role_definition(command)
        except Exception as exc:
            exc.support_class, exc.support_message = definition_support_details(exc)
            raise

    def _record_role_definition(self, command):
        """Admit once, execute once; replay uses the same receipt coordinate and no new writes."""
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES, IntakeAdmissionTechnicalFailure, IntakeEntry,
            _require_admission_state, _resolve_actor, admit_request,
        )
        entry = IntakeEntry.RECORD_ROLE_DEFINITION
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("RECORD_ROLE_DEFINITION is not active")
        shape = definition_intake_shape(command)
        bound = admit_request(self._alias, entry, _resolve_actor(self._alias, command.credential), shape)
        try:
            with authority_atomic(self._alias, received_at=bound.received_at):
                result = self._execute(command, bound)
            return result
        except S015AuthorityDenied as exc:
            disclosed = getattr(exc.disclosed_refusal_code, "value", None)
            if disclosed not in DEFINITION_AUTHORITY_MESSAGES or exc.authority_decision_reference is None:
                raise
            message, support_class = DEFINITION_AUTHORITY_MESSAGES[disclosed]
            refusal = S015AuthorityDenied(
                message, refusal_code=exc.refusal_code, disclosed_refusal_code=exc.disclosed_refusal_code,
                authority_decision_reference=exc.authority_decision_reference, request_uuid=exc.request_uuid,
            )
            refusal.support_class = support_class
            raise refusal from exc
        except DatabaseError as exc:
            primary = getattr(getattr(exc.__cause__, "diag", None), "message_primary", None)
            known = {
                "SR definition refused: a deactivated version must be superseded by a successor at commit",
                "SR definition refused: office basis citation missing",
                "SR definition refused: this office still has a holder or a pending nomination",
                "SR definition refused: custom role entitlement absent at receipt",
                "SR definition refused: governed decision does not match definition",
                "SR definition refused: retiring decision does not match definition",
                "SR definition refused: reserved root office",
            } | {f"SR definition refused: {kind} ({rel})" for kind in (
                "governance relationship citation missing", "citation without its relationship") for rel in _RELATIONSHIPS}
            if primary in known:
                raise S015RoleDefinitionGuardRefused(primary) from exc
            raise  # Technical faults are never rewritten as a business refusal.

    def _execute(self, command, bound):
        from src.intevia.services.command_intake import IntakeEntry
        actor = _lock_actor(database_alias=self._alias, credential=command.credential, unavailable=self._authority_unavailable)
        bound.verify(command, actor, IntakeEntry.RECORD_ROLE_DEFINITION, self._alias)
        if command.effective_at > bound.received_at or command.occurred_at > bound.received_at:
            raise S015RoleDefinitionMalformed("definition coordinates cannot be after receipt")
        acquire_organism_command_mutex(database_alias=self._alias, actor_identity_id=actor.identity_id,
                                      action=_ACTION, idempotency_key=bound.idempotency_key)
        # The intake fingerprint depends on requested content, not on the subsequently changed active row.
        fingerprint = hashlib.sha256(b"INTEVIA:S015:ROLE_DEFINITION_COMMAND:v1\x00" + canonical_json_bytes({
            "request_fingerprint": bound.request_fingerprint, "received_at": canonical_timestamp(bound.received_at),
        })).hexdigest()
        replay = self._attempt_replay(actor=actor, request_reference=bound.request_reference,
            payload_fingerprint=fingerprint, idempotency_key=bound.idempotency_key,
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()))
        if replay is not None:
            return replay
        try:
            organism = LivingOrganism.objects.using(self._alias).get(organism_id=command.living_organism_uuid)
        except LivingOrganism.DoesNotExist as exc:
            raise S015RoleDefinitionNotFound(ORGANISM_NOT_FOUND_MESSAGE, cause='living organism not found') from exc
        _read_actor_active_membership(database_alias=self._alias, actor_identity_pk=actor.pk,
            living_organism_pk=organism.pk, unavailable=self._authority_unavailable)
        _lock_registered_organism(database_alias=self._alias, organism_id=command.living_organism_uuid)
        organism = LivingOrganism.objects.using(self._alias).get(organism_id=command.living_organism_uuid)
        membership, roles = _lock_actor_membership_context(database_alias=self._alias,
            actor_identity_id=actor.identity_id, living_organism_id=organism.organism_id, unavailable=self._authority_unavailable)
        prior = None
        if command.role_definition_uuid is not None:
            prior = OrganismRoleDefinition.objects.using(self._alias).select_for_update().filter(
                role_uuid=command.role_definition_uuid, living_organism=organism, scope="LIVING_ORGANISM").first()
            if prior is None:
                raise S015RoleDefinitionNotFound(DEFINITION_NOT_FOUND_MESSAGE, cause='definition version not found in this LO')
        action = command.event_action
        if action == "RETIRE":
            content = DefinitionContent(**{key: getattr(prior, key) for key in DefinitionContent.__dataclass_fields__ if key != "provisions"},
                provisions=tuple(DefinitionProvision(p.relationship, p.provision_reference, p.provision_version) for p in prior.provisions.all()))
        else:
            content = command.content
        if action == "AMEND" and prior.code != content.code:
            raise S015RoleDefinitionMalformed("AMEND cannot change a code family")
        if content.code == "COORDINATOR":
            raise S015RoleDefinitionRefused('SR_COORDINATOR_DEFINITION_RESERVED', DEFINITION_REFUSAL_MESSAGES['SR_COORDINATOR_DEFINITION_RESERVED'][0])
        if content.code.startswith("INTEVIA_"):
            raise S015RoleDefinitionRefused('SR_ROOT_OFFICE_RESERVED', DEFINITION_REFUSAL_MESSAGES['SR_ROOT_OFFICE_RESERVED'][0])
        if action == "AMEND" and prior.kind != content.kind:
            raise S015RoleDefinitionRefused('SR_DEFINITION_KIND_FIXED', DEFINITION_REFUSAL_MESSAGES['SR_DEFINITION_KIND_FIXED'][0])
        if action == "RETIRE" and content.kind == "BOARD_OFFICE":
            raise S015RoleDefinitionRefused('SR_BOARD_OFFICE_PERMANENT', DEFINITION_REFUSAL_MESSAGES['SR_BOARD_OFFICE_PERMANENT'][0])
        latest = OrganismRoleDefinition.objects.using(self._alias).filter(living_organism=organism, code=content.code).order_by("-definition_version").first()
        version = prior.definition_version if action == "RETIRE" else (0 if latest is None else latest.definition_version) + 1
        content_values = asdict(content) if action == "RETIRE" else _content_dict(content)
        content_values["provisions"] = sorted(content_values["provisions"], key=lambda p: p["relationship"])
        target = {**content_values, "action": _ACTION.value, "schema": _TARGET_SCHEMA,
                  "event_action": action, "living_organism_id": str(organism.organism_id),
                  "role_uuid": None if prior is None else str(prior.role_uuid),
                  "definition_version": version,
                  "supersedes_role_uuid": str(prior.role_uuid) if action == "AMEND" else None,
                  "retirement_reason": command.retirement_reason}
        decision = self._evaluate_authority(actor=actor, actor_membership=membership, actor_role_refs=roles,
            request_reference=bound.request_reference, idempotency_key=bound.idempotency_key,
            target_fingerprint=authority_target_fingerprint(target), received_at=bound.received_at,
            effective_at=command.effective_at, target=target, living_organism_id=organism.organism_id)
        if latest is not None and latest.retired_by_decision_reference is not None:
            raise S015RoleDefinitionRefused('SR_DEFINITION_RETIRED', DEFINITION_REFUSAL_MESSAGES['SR_DEFINITION_RETIRED'][0])
        if action == "DEFINE" and latest is not None:
            raise S015RoleDefinitionRefused('SR_DEFINITION_EXISTS', DEFINITION_REFUSAL_MESSAGES['SR_DEFINITION_EXISTS'][0])
        if prior is not None and (not prior.active or latest is None or latest.pk != prior.pk):
            raise S015RoleDefinitionRefused('SR_SUPERSEDED_DEFINITION', DEFINITION_REFUSAL_MESSAGES['SR_SUPERSEDED_DEFINITION'][0])
        if action == "RETIRE":
            if _has_unended_assignment(self._alias, organism, prior.code):
                raise S015RoleDefinitionRefused('SR_RETIREMENT_HOLDER_REMAINS', DEFINITION_REFUSAL_MESSAGES['SR_RETIREMENT_HOLDER_REMAINS'][0])
            OrganismRoleDefinition.objects.using(self._alias).filter(pk=prior.pk, active=True).update(
                active=False, retired_by_decision_reference=decision.authority_decision_reference,
                retired_effective_at=command.effective_at, retired_received_at=bound.received_at,
                retirement_reason=command.retirement_reason)
            row = prior
        else:
            _check_citations(content)
            if prior is not None:
                OrganismRoleDefinition.objects.using(self._alias).filter(pk=prior.pk, active=True).update(active=False)
            row = _write_definition_version(database_alias=self._alias, organism=organism,
                **{key: getattr(content, key) for key in DefinitionContent.__dataclass_fields__},
                supersedes=prior, enterer=actor, authority_decision_reference=decision.authority_decision_reference,
                occurred_at=command.occurred_at, effective_at=command.effective_at, received_at=bound.received_at)
        result = RoleDefinitionResult(row.role_uuid, action, row.definition_version,
            decision.authority_reference, decision.authority_decision_reference,
            _lineage(row, action, decision.authority_decision_reference))
        return self._finalise(actor=actor, request_reference=bound.request_reference,
            idempotency_key=bound.idempotency_key, payload_fingerprint=fingerprint,
            result=result, received_at=bound.received_at)


__all__ = ["DefinitionContent", "DefinitionProvision", "RecordRoleDefinitionCommand", "RoleDefinitionResult",
           "OrganismRoleDefinitionService", "S015RoleDefinitionError", "S015RoleDefinitionMalformed",
           "S015RoleDefinitionNotFound", "S015RoleDefinitionAuthorityUnavailable", "S015RoleDefinitionRefused",
           "S015RoleDefinitionGuardRefused", "_write_definition_version",
           "board_office_vacancies", "BoardOfficeVacancy", "definition_support_details"]
