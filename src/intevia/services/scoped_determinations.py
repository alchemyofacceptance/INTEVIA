"""S. Consume independently authorised findings at an exact effective/knowledge cut.

This internal service does not issue constitutional mandates. The admitting
instrument names a verifier, its basis, target and expiry. Its governed grant
is a separate input. This service cannot use one of its findings as that grant.
"""
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connections, transaction

from core.models import (
    AuthorityBasis, GovernedDetermination,
)
from src.intevia.services.organism_authority import authority_target_fingerprint, canonical_timestamp

Type = GovernedDetermination.DeterminationType
TYPES = {Type.EXECUTION_ELIGIBILITY_QUALIFICATION, Type.UNAVAILABILITY_DETERMINATION,
         Type.INDEPENDENCE_DETERMINATION}


class QualificationRefused(ValueError):
    pass


def refuse(reason):
    raise QualificationRefused(
        f"The requested finding cannot be used. {reason} No authority was transferred. "
        "Obtain a current determination from a separately authorised person for this act."
    )


@dataclass(frozen=True)
class Scope:
    organism: UUID
    target: UUID
    act: str
    kind: str
    tested_actor_id: int
    beneficiary_id: int
    subject_basis: UUID

    def fingerprint(self):
        if self.kind not in TYPES or not self.act.strip() or not self.tested_actor_id or not self.beneficiary_id:
            refuse("The act, determination type or affected person is missing.")
        return authority_target_fingerprint({
            "schema": "S:scope:1", "organism": str(self.organism), "target": str(self.target),
            "act": self.act, "kind": self.kind, "tested": self.tested_actor_id,
            "beneficiary": self.beneficiary_id, "basis": str(self.subject_basis),
        })


def visible(rows, at, known):
    return rows.filter(effective_at__lte=at, received_at__lte=known, recorded_at__lte=known)


def mandate_scope(scope, verifier_id, basis):
    return authority_target_fingerprint({"schema": "S:mandate:1", "subject": scope.fingerprint(),
                                         "verifier": verifier_id, "basis": str(basis.basis_uuid)})


def expiry(text):
    try:
        instant = datetime.fromisoformat(text)
        if instant.tzinfo is None:
            raise ValueError
        return instant
    except (TypeError, ValueError):
        refuse("The cited authority has no verifiable expiry.")


def check_mandate(alias, scope, mandate_id, verifier_id, verifier_basis, at, known, until=None):
    """Use the identical validator at issuance, direct insert and consumption."""
    basis = AuthorityBasis.objects.using(alias).filter(basis_uuid=verifier_basis).first()
    grant = visible(GovernedDetermination.objects.using(alias), at, known).filter(
        determination_uuid=mandate_id, method_reference__startswith="S:instrument:1|").first()
    if not grant or not basis:
        refuse("An independently qualified mandate for this person and act is missing.")
    end = expiry(grant.method_reference.removeprefix("S:instrument:1|"))
    method = finding_method(mandate_id, verifier_basis, until or end, scope.subject_basis)
    row = dict(determiner_id=verifier_id, tested_actor_id=scope.tested_actor_id,
               safeguard_beneficiary_id=scope.beneficiary_id, scope_fingerprint=scope.fingerprint(),
               relied_upon_authority_principal_id=basis.authority_principal_id, method_reference=method,
               evidence_reference=grant.evidence_reference, independence_state="INDEPENDENT")
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT public.c2_finding_problem(jsonb_populate_record(NULL::core_governeddetermination,%s),"
                       "%s,%s,%s)", [json.dumps(row), scope.subject_basis, at, known])
        reason, = cursor.fetchone()
    if reason:
        refuse(reason)
    return basis


def finding_method(mandate, verifier_basis, until, subject_basis):
    return f"S:finding:1|{mandate}|{verifier_basis}|{canonical_timestamp(until)}|{subject_basis}"


def record(*, alias, scope, mandate_id, credential, verifier_basis, result, evidence, at, received, until):
    """Append a staff/instrument-admitted finding. This is not a public identity/credential entry point."""
    from src.intevia.services.clock import database_now

    if result not in {"QUALIFIED", "REFUSED"} or not evidence.strip() or len(evidence) > 255:
        refuse("A finding needs an explicit outcome and its evidence.")
    with transaction.atomic(using=alias):
        from src.intevia.services.organism_command_support import _lock_actor
        verifier = _lock_actor(database_alias=alias, credential=credential, unavailable=QualificationRefused)
        verifier_id = verifier.pk
        from src.intevia.services.organism_command_support import _lock_registered_organism
        _lock_registered_organism(alias, scope.organism)
        # Invalidation and this writer share the immutable basis row locks.
        list(AuthorityBasis.objects.using(alias).select_for_update().filter(
            basis_uuid__in=[scope.subject_basis, verifier_basis]).order_by("pk"))
        now = database_now(alias)
        if received > now or at > received or until <= at:
            refuse("The finding's time interval is invalid.")
        for cut, known in ((at, received), (now, now)):
            basis = check_mandate(alias, scope, mandate_id, verifier_id, verifier_basis, cut, known, until)
        return GovernedDetermination.objects.using(alias).create(
            determination_type=scope.kind, tested_actor_id=scope.tested_actor_id,
            safeguard_beneficiary_id=scope.beneficiary_id, determiner_id=verifier_id,
            relied_upon_authority_principal_id=basis.authority_principal_id,
            result=result, evidence_reference=evidence, scope_fingerprint=scope.fingerprint(),
            method_reference=finding_method(mandate_id, verifier_basis, until, scope.subject_basis),
            independence_state="INDEPENDENT", occurred_at=at, effective_at=at, received_at=received,
        )


def qualified(alias, scope, at, known):
    """Return the exact governing decision. Later refusal supersedes a positive finding."""
    row = visible(GovernedDetermination.objects.using(alias), at, known).filter(
        scope_fingerprint=scope.fingerprint(), determination_type=scope.kind,
        tested_actor_id=scope.tested_actor_id, safeguard_beneficiary_id=scope.beneficiary_id,
    ).order_by("-effective_at", "-occurred_at", "-received_at", "-recorded_at", "-pk").first()
    if not row or row.result != "QUALIFIED" or row.independence_state != "INDEPENDENT":
        refuse("There is no positive finding for this exact target.")
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT public.c2_finding_problem(d,%s,%s,%s) FROM core_governeddetermination d WHERE d.id=%s",
                       [scope.subject_basis, at, known, row.pk])
        reason, = cursor.fetchone()
    if reason:
        refuse(reason)
    return row


def invalidate(*, alias, scope, finding_id, mandate_id, credential, verifier_basis, evidence):
    """Contest one finding under an independent mandate. Never revoke its whole source basis."""
    from types import SimpleNamespace
    from core.models import DeterminationContestCase
    from src.intevia.services.organism_command_support import _lock_actor
    from src.intevia.services.clock import database_now
    from src.intevia.services.organism_relationship_event import append_relationship_event

    if not evidence.strip() or len(evidence) > 255:
        refuse("The reason for withdrawing reliance is missing or too long.")
    with transaction.atomic(using=alias):
        now = database_now(alias)
        verifier = _lock_actor(database_alias=alias, credential=credential, unavailable=QualificationRefused)
        verifier_id = verifier.pk
        from src.intevia.services.organism_command_support import _lock_registered_organism
        _lock_registered_organism(alias, scope.organism)
        finding = GovernedDetermination.objects.using(alias).select_for_update().get(
            determination_uuid=finding_id, scope_fingerprint=scope.fingerprint(), determination_type=scope.kind)
        basis = check_mandate(alias, scope, mandate_id, verifier_id, verifier_basis, now, now)
        case, created = DeterminationContestCase.objects.using(alias).get_or_create(determination=finding)
        if not created:
            return case.head_event.event_uuid
        fingerprint = authority_target_fingerprint({"finding": str(finding_id), "grant": str(mandate_id),
                                                    "act": "CONTEST", "evidence": evidence})
        command = SimpleNamespace(effective_at=now, occurred_at=now, request_reference=f"S:{finding_id}",
                                  idempotency_key=f"S:{finding_id}")
        return append_relationship_event(
            alias=alias, anchor=case, head=None, action="CONTEST", state="CONTESTED",
            actor=verifier, basis_id=basis.pk,
            decision_reference="s015d1:" + fingerprint, evidence=evidence, command=command,
            fingerprint=fingerprint, received_at=now, composing_rule=f"S:mandate:{mandate_id}",
        )
