"""Exact-time essential coverage. Readers never treat an old assessment as current standing."""
import json
from dataclasses import dataclass

from django.db import connections

from core.models import CoverageAssessment
from src.intevia.services import scoped_determinations as scoped
from src.intevia.services.organism_relationship_event import append_relationship_event


@dataclass(frozen=True)
class Coverage:
    version: int | None
    result: str
    deficits: tuple
    coordinators: frozenset
    coordinator_result: str
    occupied_coordinator: bool

    @property
    def coordinator_vacancy(self):
        return (self.coordinator_result == "UNSATISFIED" and not self.occupied_coordinator
                and ("LIVING_ORGANISM", "COORDINATOR") in self.deficits)

    @property
    def care_warning(self):
        return len(self.coordinators) == 1


def roster_at(alias, lo, at, known):
    return scoped.visible(CoverageAssessment.objects.using(alias), at, known).filter(
        case__living_organism=lo
    ).order_by("-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence").first()


def office_scope(lo, assignment, appointment_basis):
    return scoped.Scope(lo.organism_id, assignment.assignment_uuid, "COUNT_OFFICE",
                        scoped.Type.EXECUTION_ELIGIBILITY_QUALIFICATION, assignment.membership.identity_id,
                        assignment.membership.identity_id, appointment_basis.basis_uuid)


def coordinator_covered(alias, lo, at, known):
    """Only the INTEVIA Coordinator requirement controls ordinary LO capacity."""
    with connections[alias].cursor() as cursor:
        cursor.execute("""
            SELECT public.c2_result(%s, public.c2_roster_version(%s,%s,%s), %s,%s,true)='SATISFIED'
        """, [lo.pk, lo.pk, at, known, at, known])
        return cursor.fetchone()[0]


def assess(alias, lo, at, known, *, excluding=None):
    """One statement snapshot; the same roster and occupant readers serve the commit guard."""
    with connections[alias].cursor() as cursor:
        cursor.execute("""
            WITH version AS (SELECT public.c2_roster_version(%s,%s,%s) AS v),
            occupants AS MATERIALIZED (
                SELECT * FROM public.c2_seats(%s,%s,%s) WHERE membership_id IS DISTINCT FROM %s
            ), rules AS (
                SELECT r.scope, r.qualifying_role_code, r.minimum_active_occupants,
                (SELECT count(*) FROM occupants o WHERE o.role_scope=r.scope AND o.role_code=r.qualifying_role_code
                    AND o.standing='QUALIFIED') AS held,
                (SELECT count(*) FROM occupants o WHERE o.role_scope=r.scope AND o.role_code=r.qualifying_role_code
                    AND o.standing<>'REFUSED') AS possible
                FROM version, LATERAL public.c2_rules(%s,version.v,%s,%s) r
            )
            SELECT v, (SELECT count(*) FROM rules),
            (SELECT jsonb_agg(jsonb_build_array(scope,qualifying_role_code) ORDER BY scope,qualifying_role_code)
             FROM rules WHERE held<minimum_active_occupants),
            ARRAY(SELECT membership_id FROM occupants WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR'
                  AND standing='QUALIFIED'),
            EXISTS(SELECT FROM occupants WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR'),
            EXISTS(SELECT FROM rules WHERE possible<minimum_active_occupants),
            public.c2_legacy_uncertain(%s,v,%s,%s),
            public.c2_result(%s,v,%s,%s,true,%s)
            FROM version""", [lo.pk, at, known, lo.pk, at, known, excluding, lo.pk, at, known, lo.pk, at, known, lo.pk, at, known, excluding])
        version, rule_count, deficits, coordinators, occupied, definite_shortage, uncertain_history, coordinator_result = cursor.fetchone()
    if isinstance(deficits, str):
        deficits = json.loads(deficits)
    result = "UNSATISFIED" if definite_shortage else ("UNKNOWN" if not rule_count or deficits else "SATISFIED")
    if uncertain_history:
        result = "UNKNOWN"
    return Coverage(version, result, tuple(tuple(pair) for pair in (deficits or [])), frozenset(coordinators), coordinator_result, occupied)


@dataclass(frozen=True)
class Cause:
    actor: object
    basis_id: int
    decision_reference: str
    evidence: str
    command: object
    fingerprint: str
    received_at: object

    def append(self, alias, **fields):
        return append_relationship_event(
            alias=alias, actor=self.actor, basis_id=self.basis_id, decision_reference=self.decision_reference,
            evidence=self.evidence, command=self.command, fingerprint=self.fingerprint,
            received_at=self.received_at, **fields,
        )


def record_coverage(alias, lo, cause):
    """Caller owns the LO lock and transaction; include every sibling effect already written."""
    command = cause.command
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT public.c2_publish(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                       [lo.pk, command.effective_at, command.occurred_at, cause.received_at,
                        cause.actor.pk, cause.basis_id, cause.decision_reference, cause.evidence,
                        command.idempotency_key, cause.fingerprint])
        event_id, = cursor.fetchone()
    event = CoverageAssessment.objects.using(alias).get(pk=event_id)
    return assess(alias, lo, command.effective_at, event.evaluated_known_at), event.event_uuid
