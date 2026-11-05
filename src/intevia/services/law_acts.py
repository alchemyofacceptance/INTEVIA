"""Authority-atomic LAW acts. The authenticated command is separate from its preview."""

from dataclasses import asdict, dataclass
import json
from uuid import UUID

from django.db import connections
from django.utils import timezone

from core import models
from core.organism_contract import OrganismAction
from src.intevia.services.command_intake import IntakeShape, _resolve_actor, admit_request
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import (
    S015Authority, S015AuthorityDenied, S015PostFoundingAuthorityRequest,
    S015PreFoundingAuthorityRequest, authority_target_fingerprint,
)
from src.intevia.services.organism_command_support import (
    ReplayResultType, ReplayViewer, S015CommandReceiptMalformed, S015CommandReplayMismatch,
    _canonical_key, _canonical_uuid, _lock_actor, _lock_registered_organism,
    acquire_organism_command_mutex, authority_atomic, serve_replay,
)


class LawUnavailable(RuntimeError):
    """The presenter has no current identity or the requested target is unavailable."""


@dataclass(frozen=True, slots=True, kw_only=True)
class LawProposal:
    organism: UUID
    resource: str
    proposal: UUID
    law_class: str
    operation: str
    intent: str
    version: int | None = None
    predecessor: UUID | None = None
    scope: str = "LIVING_ORGANISM"
    circle: UUID | None = None
    consequence_class: str = "BINDING"
    reserves_bylaws: bool = False
    resolution_effect: str | None = None
    resolution_reference: str | None = None
    requested_route: str | None = None
    feature: str | None = None
    affected_reference: str | None = None
    resolution: str | None = None

    def content(self):
        for name in ("organism", "proposal", "predecessor", "circle"):
            value = getattr(self, name)
            if value is not None or name in ("organism", "proposal"):
                _canonical_uuid(value, name)
        for name in ("resource", "law_class", "operation", "intent", "scope", "consequence_class"):
            _canonical_key(getattr(self, name), name, 120)
        for name in ("resolution_effect", "resolution_reference", "requested_route", "feature",
                     "affected_reference", "resolution"):
            value = getattr(self, name)
            if value is not None:
                _canonical_key(value, name, 255)
        if self.version is not None and (type(self.version) is not int or not 1 <= self.version <= 2147483647):
            raise ValueError("A LIBRARY version is a positive integer.")
        if type(self.reserves_bylaws) is not bool:
            raise TypeError("An Article reservation must be true or false.")
        return {k: str(v) if isinstance(v, UUID) else v for k, v in asdict(self).items()}


@dataclass(frozen=True, slots=True, kw_only=True)
class AdoptLawCommand:
    credential: object
    idempotency_key: str
    request_reference: str
    proposal: LawProposal


def law_shape(command):
    if type(command) is not AdoptLawCommand or type(command.proposal) is not LawProposal:
        raise TypeError("Use an AdoptLawCommand with one LawProposal.")
    spec = command.proposal.content()
    return IntakeShape(_canonical_key(command.idempotency_key, "idempotency_key", 120),
                       _canonical_key(command.request_reference, "request_reference", 128),
                       {"organism": spec["organism"], "resource": spec["resource"]}, spec, (), None)


def _assess(cursor, spec, actor_id, cut=None, received=None):
    cursor.execute(
        """WITH t AS (SELECT COALESCE(%s::timestamptz,statement_timestamp()) AS cut)
           SELECT cut,public.l_assess_request(%s::jsonb,%s,COALESCE(%s::timestamptz,cut),cut)::text FROM t""",
        [cut, json.dumps(spec), actor_id, received],
    )
    at, plan = cursor.fetchone()
    return at, json.loads(plan)


def law_authority_facts(request):
    """Re-evaluate locked facts independently of the plan submitted to the provider."""
    target = request.target
    if (request.command_subtype != "ADOPT_LAW/LAW_VERSION" or type(target) is not dict
            or set(target) != {"spec", "plan", "intake", "fingerprint", "living_organism_id"}
            or str(request.living_organism_id) != target["living_organism_id"]
            or target["spec"].get("organism") != target["living_organism_id"]):
        return None
    with connections[request.database_alias].cursor() as cursor:
        _, plan = _assess(cursor, target["spec"], request.actor_pk, request.effective_at, request.evaluated_at)
    if plan != target["plan"] or "reason" in plan:
        return None
    return {"kind": "LAW_VERSION", "route": plan["route"]}


@dataclass(frozen=True, slots=True)
class LawResult:
    status: str
    message: str | None = None
    event: str | None = None
    proposal: str | None = None
    route: str | None = None
    affected_reference: str | None = None

    def replay_field_audiences(self):
        # All returned fields describe the presenting actor's own request. No voter,
        # office PK, individual nomination or private clause is in the receipt.
        return {}


def _serve(receipt, actor, alias, cursor):
    cursor.execute(
        """SELECT public.l_receipt_payload(d)::text FROM public.core_authoritydecision d
            JOIN public.core_commandintake i ON i.id=(d.target->>'intake')::bigint
            WHERE d.decision_reference=%s AND d.actor_id=%s AND public.l_intake_matches(d,i)""",
        [receipt.authority_decision_reference, actor.pk],
    )
    row = cursor.fetchone()
    if not row or row[0] is None or json.loads(row[0]) != receipt.result_payload:
        raise S015CommandReceiptMalformed()
    result = LawResult(**receipt.result_payload)
    return serve_replay(result, viewer=ReplayViewer(actor.pk, actor.identity_id, timezone.now()),
                        result_type=ReplayResultType(LawResult, alias))


class OrganismLawService:
    def __init__(self, *, database_alias="default"):
        self.alias = database_alias
        self.authority = S015Authority(database_alias=database_alias, provider=ProductionApplicationAuthorityProvider(
            database_alias=database_alias))

    def preview_adoption(self, *, credential, proposal):
        """Advisory read. Return exact dependencies only after the presenter passes the route check."""
        actor = _resolve_actor(self.alias, credential)
        with connections[self.alias].cursor() as cursor:
            at, plan = _assess(cursor, proposal.content(), actor.pk)
        result = {"as_of": at, "message": plan.get("reason"), "route": plan.get("route")}
        for field in ("affected_reference", "affected_count", "affected"):
            if field in plan:
                result[field] = plan[field]
        return result

    def adopt_law(self, command):
        """Admit durably, lock, decide, write the ILC and finalise one actor-bound receipt."""
        return self._execute_admitted(command, self._admit(command))

    def _admit(self, command):
        return admit_request(self.alias, models.IntakeEntry.ADOPT_LAW,
                             _resolve_actor(self.alias, command.credential), law_shape(command))

    def _execute_admitted(self, command, bound):
        entry = models.IntakeEntry.ADOPT_LAW
        alias = self.alias
        shape = law_shape(command)
        with authority_atomic(alias, received_at=bound.received_at), connections[alias].cursor() as cursor:
            actor = _lock_actor(database_alias=alias, credential=command.credential, unavailable=LawUnavailable)
            bound.verify(command, actor, entry, alias)
            acquire_organism_command_mutex(database_alias=alias, actor_identity_id=actor.identity_id,
                                          action=OrganismAction.ADOPT_LAW, idempotency_key=bound.idempotency_key)
            receipts = models.OrganismCommandReceipt.objects.using(alias)
            coordinate = dict(actor=actor, action=entry.value, idempotency_key=bound.idempotency_key)
            existing = receipts.filter(**coordinate).first()
            if existing is not None:
                if (existing.payload_fingerprint != bound.request_fingerprint
                        or existing.request_reference != bound.request_reference):
                    raise S015CommandReplayMismatch()
                return _serve(existing, actor, alias, cursor)

            spec = shape.command
            organism = command.proposal.organism
            _lock_registered_organism(database_alias=alias, organism_id=organism)
            lo = models.LivingOrganism.objects.using(alias).select_for_update().filter(organism_id=organism).first()
            if lo is None:
                raise LawUnavailable("Choose an existing Living Organism.")
            models.LibraryResource.objects.using(alias).select_for_update().filter(resource_id=spec["resource"]).first()
            cursor.execute("SELECT public.l_lock_dependencies(%s::jsonb,%s,%s)", [json.dumps(spec), lo.pk, actor.pk])
            cut, plan = _assess(cursor, spec, actor.pk, received=bound.received_at)
            target = dict(spec=spec, plan=plan, intake=bound.intake_id, fingerprint=bound.request_fingerprint,
                          living_organism_id=str(organism))
            membership = models.OrganismMembership.objects.using(alias).filter(
                living_organism=lo, identity=actor, state="ACTIVE").select_related("head_transition").first()
            fields = dict(database_alias=alias, actor_pk=actor.pk, actor_identity_id=actor.identity_id,
                          actor_access_epoch=actor.access_epoch, action=OrganismAction.ADOPT_LAW,
                          command_subtype="ADOPT_LAW/LAW_VERSION", target_fingerprint=authority_target_fingerprint(target),
                          request_reference=bound.request_reference, idempotency_key=bound.idempotency_key,
                          evaluated_at=bound.received_at, effective_at=cut, target=target, living_organism_id=organism)
            if membership is None:
                request = S015PreFoundingAuthorityRequest(**fields)
            else:
                request = S015PostFoundingAuthorityRequest(**fields,
                    active_membership_head_reference=membership.head_transition.lineage_reference,
                    contextual_role_head_references=())
            try:
                decision = self.authority.qualify(request)
            except S015AuthorityDenied as exc:
                reference = exc.authority_decision_reference
                if reference is None:
                    raise
            else:
                reference = decision.authority_decision_reference
                cursor.execute("SELECT public.l_execute(%s)", [reference])
            cursor.execute(
                """SELECT public.l_receipt_payload(d)::text,coalesce(e.lineage_reference,
                    's015l1:'||encode(sha256(convert_to(d.decision_reference||':LAW:REFUSED','UTF8')),'hex'))
                    FROM public.core_authoritydecision d LEFT JOIN public.core_lawact a ON a.decision_id=d.id
                    LEFT JOIN public.core_livingorganismevent e ON e.id=a.event_id WHERE d.decision_reference=%s""",
                [reference],
            )
            payload, lineage = cursor.fetchone()
            receipt = receipts.create(**coordinate, request_reference=bound.request_reference,
                payload_fingerprint=bound.request_fingerprint, authority_decision_reference=reference,
                lineage_reference=lineage, result_payload=json.loads(payload))
            return _serve(receipt, actor, alias, cursor)
