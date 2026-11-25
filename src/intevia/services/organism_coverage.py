"""Exact-time essential coverage. Readers never treat an old assessment as current standing."""
import json
from dataclasses import dataclass

from django.db import connections

from core.models import CoverageAssessment
from src.intevia.services import scoped_determinations as scoped
from src.intevia.services.organism_relationship_event import append_relationship_event


@dataclass(frozen=True)
class Coverage:
    """One LO's essential coverage at one cut, as assess reads it.

    version is the roster version read (None if none is found).
    result is SATISFIED, UNSATISFIED or UNKNOWN, as assess
    decides it. deficits holds (scope, role code) pairs whose
    QUALIFIED holders fall below the rule's minimum. coordinators
    holds membership IDs of QUALIFIED LO Coordinator seats.
    coordinator_result is c2_result's answer for the LO
    Coordinator rule alone. occupied_coordinator is True when an
    LO Coordinator seat is read in any standing. Seats leave out
    the membership assess was told to exclude. The instance is
    frozen; construction checks nothing.
    """
    version: int | None
    result: str
    deficits: tuple
    coordinators: frozenset
    coordinator_result: str
    occupied_coordinator: bool

    @property
    def coordinator_vacancy(self):
        """Is the LO without any LO Coordinator?

        True only when all three hold: coordinator_result is
        UNSATISFIED, no LO Coordinator seat is read in any standing,
        and (LIVING_ORGANISM, COORDINATOR) is among the deficits. A
        seat read but not QUALIFIED is not a vacancy.
        """
        return (self.coordinator_result == "UNSATISFIED" and not self.occupied_coordinator
                and ("LIVING_ORGANISM", "COORDINATOR") in self.deficits)

    @property
    def care_warning(self):
        """Is exactly one QUALIFIED LO Coordinator counted?

        Reads the coordinators field only: no other office, no other
        standing and nothing from the database.
        """
        return len(self.coordinators) == 1


def roster_at(alias, lo, at, known):
    """Return the LO's latest coverage assessment visible at a cut.

    Read on alias. The case must belong to the LO, effective_at
    must be at or before at, and received_at and recorded_at must
    both be at or before known. Order by effective_at, occurred_at,
    received_at, recorded_at and sequence, all descending.
    Return the first row, or None. No row lock is requested.
    """
    return scoped.visible(CoverageAssessment.objects.using(alias), at, known).filter(
        case__living_organism=lo
    ).order_by("-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-sequence").first()


def office_scope(lo, assignment, appointment_basis):
    """Build the COUNT_OFFICE scope for one assignment.

    Use the LO's organism ID, the assignment UUID and the
    appointment basis UUID. The kind is
    EXECUTION_ELIGIBILITY_QUALIFICATION. The holder's membership
    supplies the identity ID for both tested actor and beneficiary.
    Return the Scope without validating it or writing anything.
    """
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
    """Who and what caused a coverage change, for its event writes.

    Hold the actor, authority basis ID, decision reference,
    evidence, command, request fingerprint and receipt time.
    record_coverage reads the command's effective_at, occurred_at
    and idempotency_key. The instance is frozen; construction
    checks nothing.
    """
    actor: object
    basis_id: int
    decision_reference: str
    evidence: str
    command: object
    fingerprint: str
    received_at: object

    def append(self, alias, **fields):
        """Append a relationship event and return its event UUID.

        Forward the alias and caller's event fields with this cause's
        actor, basis ID, decision reference, evidence, command,
        fingerprint and receipt time to append_relationship_event.
        The writer's errors propagate.
        """
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
