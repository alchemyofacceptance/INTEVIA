"""C3 authority lifecycle services under 00_COMMISSION_C3_v1_1.md.
Resolve the pinned LO application, evaluate the holder at the act and record only instrument-authorised changes.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import re
from types import SimpleNamespace
import unicodedata
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist

from django.db import IntegrityError, connections, transaction

from core import models
from src.intevia.services import scoped_determinations as s
from src.intevia.services.clock import database_now
from src.intevia.services.organism_authority import authority_target_fingerprint
from src.intevia.services.organism_command_support import _lock_actor, _lock_registered_organism
from src.intevia.services.organism_relationship_event import append_relationship_event


@dataclass(frozen=True)
class HeldWork:
    application_uuid: UUID
    rule: str
    organism_uuid: UUID
    route: str
    reason: str


class CaseHeld(s.QualificationRefused):
    def __init__(self, application, reason):
        self.hold_reason = reason
        message = ("Your application is waiting for someone qualified to decide it. "
                   "Your answer and the rules it was submitted under are preserved. "
                   "You can ask your organisation’s application processors about the handover.")
        self.result = HeldWork(application.application_uuid, first_engagement(application).settings_basis,
                               application.living_organism.organism_id, "EXISTING_QUALIFIED_GOVERNANCE", message)
        super().__init__(message)


@contextmanager
def governed_write(alias, application_uuid=None):
    """Translate known guard conflicts after rollback; leave unrelated infrastructure failures visible."""
    try:
        yield
    except IntegrityError as error:
        if connections[alias].in_atomic_block:
            raise
        cause = error.__cause__
        diagnostic = getattr(cause, "diag", None)
        message = getattr(diagnostic, "message_primary", "") or ""
        state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        if state == "23514" and message.startswith(("C3 ", "S finding")):
            if application_uuid is not None:
                app = models.Application.objects.using(alias).filter(application_uuid=application_uuid).first()
                if app is not None and app.engagements.using(alias).filter(engagement_ordinal=1).exists():
                    raise CaseHeld(app, "The authority evidence changed before this act completed.") from None
            raise s.QualificationRefused("The instrument no longer qualifies for this act. Check the recorded authority.") from None
        if (state == "23505" and application_uuid is None
                and getattr(diagnostic, "table_name", None) == "core_authoritybasis"):
            raise s.QualificationRefused("The successor instrument is already recorded. Check its basis reference.") from None
        raise


def first_engagement(application):
    """Read the immutable first engagement which pins the governing versions."""
    return application.engagements.using(application._state.db).get(engagement_ordinal=1)


def scalar(alias, sql, values):
    """Return the first column of a one-row database check on the supplied alias."""
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, values)
        return cursor.fetchone()[0]


def lock_sources(alias, claim, office_id, *incoming):
    """After the application lock, acquire all participating source rows in primary-key order."""
    scalar(alias, "SELECT public.c3_lock_sources(%s,%s,%s::uuid[])",
           [claim.application_id, office_id, [str(value) for value in incoming if value is not None]])


def require_holder(alias, claim, at, known):
    """Check the locked claim at receipt and again at the live cut. Raise CaseHeld at either refusal."""
    lock_sources(alias, claim, claim.assignee_office_id)
    now = database_now(alias)
    for effect, cut in ((at, known), (now, now)):
        reason = scalar(alias, "SELECT public.c3_act_problem(c,%s,%s) FROM public.core_csaassignment c WHERE id=%s",
                        [effect, cut, claim.pk])
        if reason:
            raise CaseHeld(claim.application, reason)


def held_at(alias, application, now):
    """Return the public hold for an authorised reader at this cut. Closed or actionable cases have no queue entry."""
    if (models.ApplicationDecision.objects.using(alias).filter(application=application).exists()
            or models.ApplicationWithdrawal.objects.using(alias).filter(application=application).exists()):
        return None
    engagements = application.engagements.using(alias).order_by("-engagement_ordinal")
    head = engagements.first()
    if head is None or head.route != "LO_CSA":
        return None
    first = first_engagement(application)
    claim = models.CSAAssignment.objects.using(alias).filter(application=application,
            assignment_uuid=head.engagement_reference, ending__isnull=True).first()
    if claim is None or not scalar(alias, "SELECT public.c3_pin_valid(%s,%s,%s)",
                                   [application.pk, first.settings_basis, first.deciding_party_id]):
        reason = "The original authority or governing versions need confirmation."
    else:
        reason = scalar(alias, "SELECT public.c3_act_problem(c,%s,%s) FROM public.core_csaassignment c WHERE id=%s",
                        [now, now, claim.pk])
    if reason is None:
        return None
    return CaseHeld(application, reason).result


def replacement_finding(alias, claim, office, person, at, known):
    """Lock the relevant sources, confirm displacement and return the same qualified finding at both cuts."""
    lock_sources(alias, claim, office.pk)
    ids = []
    now = database_now(alias)
    for effect, cut in ((at, known), (now, now)):
        displaced = scalar(alias, "SELECT public.c3_act_problem(c,%s,%s) FROM public.core_csaassignment c WHERE id=%s",
                           [effect, cut, claim.pk])
        if displaced is None:
            raise s.QualificationRefused("The recorded holder can still act on this application. "
                                         "Record the basis ending or the case-specific reason for handover first.")
        ids.append(scalar(alias, "SELECT public.c3_replacement(%s,%s,%s,%s,%s,%s)",
                          [claim.application_id, claim.pk, office.pk, person.pk, effect, cut]))
    if ids[0] is None or ids[0] != ids[1]:
        raise CaseHeld(claim.application, "There is no current independent qualification for this replacement.")
    return ids[0]


def _open_claim(alias, app_uuid):
    """Lock the owning LO and case and return the open LO_CSA claim under the original rule."""
    try:
        application = models.Application.objects.using(alias).select_related("living_organism").get(application_uuid=app_uuid)
    except models.Application.DoesNotExist:
        raise s.QualificationRefused("The application could not be found. Check the application reference.") from None
    _lock_registered_organism(alias, application.living_organism.organism_id)
    application = models.Application.objects.using(alias).select_for_update().get(pk=application.pk)
    if (models.ApplicationDecision.objects.using(alias).filter(application=application).exists()
            or models.ApplicationWithdrawal.objects.using(alias).filter(application=application).exists()):
        raise s.QualificationRefused("The application is already closed. Its recorded outcome stands.")
    engagements = application.engagements.using(alias).order_by("-engagement_ordinal")
    latest = engagements.first()
    if latest is None or latest.route != "LO_CSA":
        raise s.QualificationRefused("The application has no Coordinator engagement to review.")
    first = first_engagement(application)
    if not scalar(alias, "SELECT public.c3_pin_valid(%s,%s,%s)", [application.pk, first.settings_basis,
                                                                  first.deciding_party_id]):
        raise CaseHeld(application, "The recorded governing versions do not agree.")
    claim = models.CSAAssignment.objects.using(alias).filter(
        application=application, assignment_uuid=latest.engagement_reference, ending__isnull=True).first()
    if claim is None:
        raise CaseHeld(application, "The current engagement has no open assignment.")
    return claim


def _source(alias, claim):
    """Resolve the claim’s recorded authority, holding the case when the source is unknown."""
    pk = scalar(alias, "SELECT public.c3_claim_basis(c) FROM public.core_csaassignment c WHERE id=%s", [claim.pk])
    if pk is None:
        raise CaseHeld(claim.application, "The assignment has no identifiable governing authority.")
    return models.AuthorityBasis.objects.using(alias).get(pk=pk)


def scope_for(claim, purpose, person_id, basis_uuid, kind):
    """Bind the act, tested person, basis and applicant to the first engagement of this case."""
    app = claim.application
    first = first_engagement(app)
    return s.Scope(app.living_organism.organism_id, app.application_uuid,
                   f"C3:{first.engagement_reference}:{purpose}", kind, person_id, app.applicant_id, basis_uuid)


def _purpose(claim, office):
    """Identify the displaced assignment and incoming office for this handover."""
    return f"REPLACE:{claim.assignment_uuid}:{office.assignment_uuid}"


def record_case_finding(*, alias, application_uuid, operation, credential, mandate_id,
                        verifier_basis, evidence, until, result="QUALIFIED", office_uuid=None,
                        candidate_basis=None, tested_actor_id=None):
    """Record a third-party finding, or the reviewer's own procedural act under a separate exact mandate.

    RECUSE and UNAVAILABLE occupy one case-availability stream. A separately
    qualified AVAILABLE finding can release it under a live subject basis supplied as candidate_basis.
    The actor recording the finding cannot certify their own benefit.
    """
    with governed_write(alias, application_uuid), transaction.atomic(using=alias):
        reviewer = _lock_actor(database_alias=alias, credential=credential, unavailable=s.QualificationRefused)
        claim = _open_claim(alias, application_uuid)
        if operation == "REPLACEMENT":
            try:
                office = models.ContextualRoleAssignment.objects.using(alias).select_related("membership").get(
                    assignment_uuid=office_uuid, membership__living_organism_id=claim.application.living_organism_id)
                source = models.AuthorityBasis.objects.using(alias).get(basis_uuid=candidate_basis)
            except ObjectDoesNotExist:
                raise CaseHeld(claim.application, "The proposed office or case instrument cannot be located.") from None
            lock_sources(alias, claim, office.pk, verifier_basis, candidate_basis)
            now = database_now(alias)
            person = office.membership.identity_id
            if (reviewer.pk == claim.actor_id or person in (claim.actor_id, claim.application.applicant_id)
                    or not scalar(alias, "SELECT public.c3_independent(%s,%s,%s)", [source.pk, _source(alias, claim).pk, now])
                    or not scalar(alias, "SELECT public.c3_seat(%s,%s,%s,%s,%s)",
                                  [claim.application_id, office.pk, person, now, now])):
                raise CaseHeld(claim.application, "The proposed decider lacks a separate basis or effective office.")
            scope = scope_for(claim, _purpose(claim, office), person, source.basis_uuid,
                              s.Type.EXECUTION_ELIGIBILITY_QUALIFICATION)
        elif operation in {"RECUSE", "UNAVAILABLE", "AVAILABLE", "PROCEDURAL_REVIEW"}:
            lock_sources(alias, claim, claim.assignee_office_id, verifier_basis, candidate_basis)
            now = database_now(alias)
            review = operation == "PROCEDURAL_REVIEW"
            if review and not scalar(alias, "SELECT public.c3_available(%s,%s,%s,%s)",
                                     [claim.application_id, reviewer.pk, now, now]):
                raise CaseHeld(claim.application, "This reviewer has stepped aside on this application.")
            subject_uuid = candidate_basis or _source(alias, claim).basis_uuid
            target_person = claim.actor_id if review or tested_actor_id is None else tested_actor_id
            if review:
                original = _source(alias, claim)
                verifier_basis_obj = models.AuthorityBasis.objects.using(alias).filter(basis_uuid=verifier_basis).first()
                if verifier_basis_obj is None or not scalar(alias, "SELECT public.c3_independent(%s,%s,%s)",
                                                  [verifier_basis_obj.pk, original.pk, now]):
                    raise s.QualificationRefused("The reviewer and the case holder may rely on the same source. "
                                                 "Seek an independently authorised reviewer.")
            purpose = f"REVIEW:{claim.assignment_uuid}" if review else "STEP_ASIDE"
            scope = scope_for(claim, purpose, target_person, subject_uuid,
                              s.Type.INDEPENDENCE_DETERMINATION if review else s.Type.UNAVAILABILITY_DETERMINATION)
            if not review:
                result = "REFUSED" if operation == "AVAILABLE" else "QUALIFIED"
        else:
            raise s.QualificationRefused("The requested case act is unknown.")
        if not evidence.strip():
            raise s.QualificationRefused("Give the reference for this finding.")
        recorded_evidence = f"C3:{operation}:{application_uuid}:{evidence}"
        previous = models.GovernedDetermination.objects.using(alias).filter(
            scope_fingerprint=scope.fingerprint(), determiner=reviewer, result=result,
            evidence_reference=recorded_evidence,
            method_reference=s.finding_method(mandate_id, verifier_basis, until, scope.subject_basis)).first()
        if previous:
            return previous
        return s.record(alias=alias, scope=scope, mandate_id=mandate_id, credential=credential,
                        verifier_basis=verifier_basis, result=result, evidence=recorded_evidence,
                        at=now, received=now, until=until)


@dataclass(frozen=True)
class SuccessorInstrument:
    """Exact externally supplied instrument. Its issuer is the authenticated operator named by the mandate."""
    basis_uuid: UUID
    principal_uuid: UUID
    root_uuid: UUID
    authority_class: str
    scope_fingerprint: str
    instrument_reference: str
    evidence_reference: str

    def __post_init__(self):
        if any(not isinstance(value, UUID) for value in (self.basis_uuid, self.principal_uuid, self.root_uuid)):
            raise s.QualificationRefused("The successor instrument must identify its basis, principal and root.")
        for value, limit in ((self.authority_class, 64), (self.instrument_reference, 255), (self.evidence_reference, 255)):
            if (not isinstance(value, str) or not value or len(value) > limit or value != value.strip()
                    or unicodedata.normalize("NFC", value) != value or any(ord(char) < 32 for char in value)):
                raise s.QualificationRefused("The successor instrument has an incomplete or non-canonical reference.")
        if not isinstance(self.scope_fingerprint, str) or not re.fullmatch("[a-f0-9]{64}", self.scope_fingerprint):
            raise s.QualificationRefused("The successor instrument must identify its precise scope.")

    def fingerprint(self):
        return authority_target_fingerprint({k: str(v) for k, v in vars(self).items()})


def end_scope(organism_uuid, basis, successor=None):
    """Bind a whole-basis mandate to this LO, basis and exact successor terms."""
    act = "C3:END_WHOLE_BASIS" if successor is None else "C3:SUPERSEDE:" + successor.fingerprint()
    return s.Scope(organism_uuid, basis.basis_uuid, act, s.Type.EXECUTION_ELIGIBILITY_QUALIFICATION,
                   basis.issuer_id, basis.issuer_id, basis.basis_uuid)


def end_basis(*, alias, organism_uuid, basis_uuid, credential, mandate_id, verifier_basis, evidence,
              successor=None):
    """Apply an instrument admitting this exact whole-basis act. A case recusal cannot call it by implication."""
    if not evidence.strip() or len(evidence) > 225:
        raise s.QualificationRefused("Give a reference for the instrument ending the authority.")
    with governed_write(alias), transaction.atomic(using=alias):
        actor = _lock_actor(database_alias=alias, credential=credential, unavailable=s.QualificationRefused)
        _lock_registered_organism(alias, organism_uuid)
        locked = {b.basis_uuid: b for b in models.AuthorityBasis.objects.using(alias).select_for_update().filter(
            basis_uuid__in=[basis_uuid, verifier_basis]).order_by("pk")}
        basis = locked.get(UUID(str(basis_uuid)))
        if basis is None:
            raise s.QualificationRefused("The cited authority basis cannot be located.")
        scope = end_scope(organism_uuid, basis, successor)
        now = database_now(alias)
        fingerprint = authority_target_fingerprint({"scope": scope.fingerprint(), "mandate": str(mandate_id),
                                                    "actor": actor.pk, "evidence": evidence})
        prior = models.AuthorityInvalidationEvent.objects.using(alias).filter(
            basis=basis, payload_fingerprint=fingerprint).first()
        if prior:
            return prior.event_uuid
        ended = models.AuthorityInvalidationEvent.objects.using(alias).filter(
            basis=basis, kind__in=("INVALIDATION", "SUPERSESSION"), request_reference__startswith="C3:"
        ).order_by("sequence").first()
        if ended:
            raise s.QualificationRefused(f"This authority ended on {ended.effective_at.date().isoformat()}; its record stands.")
        authoriser = s.check_mandate(alias, scope, mandate_id, actor.pk, verifier_basis, now, now)
        authority_ref = f"C3:{organism_uuid}:{mandate_id}:{successor.fingerprint() if successor else 'END'}"
        command = SimpleNamespace(occurred_at=now, effective_at=now, request_reference=authority_ref,
                                  idempotency_key="C3:"+fingerprint)
        kind = "SUPERSESSION" if successor else "INVALIDATION"
        event = append_relationship_event(alias=alias, anchor=basis, head=basis.head_invalidation_event,
            action="SUPERSEDE" if successor else "INVALIDATE", state="SUPERSEDED" if successor else "INVALIDATED",
            actor=actor, basis_id=authoriser.pk, decision_reference="s015d1:"+fingerprint,
            evidence="C3:"+evidence, command=command, fingerprint=fingerprint, received_at=now,
            invalidation_kind=kind)
        if successor:
            principals = models.AuthorityPrincipal.objects.using(alias)
            try:
                principal = principals.get(principal_uuid=successor.principal_uuid)
                root = principals.get(principal_uuid=successor.root_uuid)
            except models.AuthorityPrincipal.DoesNotExist:
                raise s.QualificationRefused("The successor principal or its root could not be found. "
                                             "Check the instrument references.") from None
            with connections[alias].cursor() as cursor:
                cursor.execute("""
                    INSERT INTO public.core_authoritybasis
                      (basis_uuid,issuer_id,superseded_basis_id,authority_principal_id,derivation_root_principal_id,
                       authority_class,scope_fingerprint,instrument_reference,evidence_reference,
                       issued_at,effective_at,received_at,recorded_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,statement_timestamp())
                """, [successor.basis_uuid, actor.pk, basis.pk, principal.pk, root.pk, successor.authority_class,
                      successor.scope_fingerprint, successor.instrument_reference, successor.evidence_reference,
                      now, now, now])
        return event


def due_holds(alias, organism_pk, since):
    """Internal worker projection. Return due case UUIDs and source instants since the last scan.

    Idempotent observation only. The case queue derives from the underlying records,
    so a process restart, repeated scan or missed tick cannot grant authority.
    """
    now = database_now(alias)
    with connections[alias].cursor() as cursor:
        cursor.execute("""
            WITH open_claim AS (
                SELECT c AS claim,a.application_uuid FROM public.core_csaassignment c
                JOIN public.core_application a ON a.id=c.application_id AND a.living_organism_id=%s
                WHERE NOT EXISTS (SELECT FROM public.core_csaassignmentend e WHERE e.assignment_id=c.id)
                  AND NOT EXISTS (SELECT FROM public.core_applicationdecision d WHERE d.application_id=a.id)
                  AND NOT EXISTS (SELECT FROM public.core_applicationwithdrawal w WHERE w.application_id=a.id)
            ), boundaries AS (
                SELECT o.claim,o.application_uuid,i.due,i.source,i.source_known FROM open_claim o
                JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=(o.claim).assignee_office_id
                JOIN public.c2_timed_input i ON i.lo_id=%s AND i.source='office-term:'||e.event_uuid
                UNION ALL
                SELECT o.claim,o.application_uuid,split_part(f.method_reference,'|',4)::timestamptz,
                       'finding-end:'||f.determination_uuid,f.recorded_at
                FROM open_claim o JOIN public.core_governeddetermination f ON f.id=(o.claim).lifecycle_finding_id
            )
            SELECT application_uuid,due,source FROM boundaries
            WHERE greatest(due,source_known)>%s AND due<=%s AND source_known<=%s
            AND (claim).effective_at<=due AND public.c3_act_problem(claim,%s,%s) IS NOT NULL
            ORDER BY due,application_uuid,source
        """, [organism_pk, organism_pk, since, now, now, now, now])
        return tuple(cursor.fetchall())
