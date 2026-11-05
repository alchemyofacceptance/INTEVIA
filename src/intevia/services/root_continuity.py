"""Governed root values, factual confirmation and consumption of constitutional grants."""
from hashlib import sha256
from uuid import UUID

from django.db import connections

from core import models
from src.intevia.services.circle_commands import command_context
from src.intevia.services.organism_authority import canonical_timestamp
from src.intevia.services.scoped_determinations import Scope


class ContinuityRefused(ValueError):
    """A requested root act could not establish its current authority."""


def query(alias, sql, *parameters):
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchone()[0]


def require_request(key):
    if not isinstance(key, UUID):
        raise ContinuityRefused("A request UUID is required.")


def replay(model, alias, key, **expected):
    previous = model.objects.using(alias).filter(request_uuid=key).first()
    if previous is not None and any(getattr(previous, k) != v for k, v in expected.items()):
        raise ContinuityRefused("This request already records a different act.")
    return previous


def set_value(*, credential, organism_uuid, setting, person_uuid, request_uuid, alias="default"):
    require_request(request_uuid)
    if setting not in ("CONFIRMER", "EMERGENCY_STEWARD"):
        raise ContinuityRefused("Choose the confirmer or the Emergency Steward.")
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        office = query(alias, "SELECT public.r_founder_office(%s,%s,%s,%s)", lo.pk, actor.pk, at, at)
        if office is None:
            raise ContinuityRefused("Only the effective Founder Steward may change root values.")
        person = models.Identity.objects.using(alias).filter(identity_id=person_uuid, access_state="active").first()
        if person is None:
            raise ContinuityRefused("The named person's active identity is required.")
        values = models.RootValueDecision.objects.using(alias).filter(organism=lo)
        old = replay(models.RootValueDecision, alias, request_uuid, organism_id=lo.pk,
                     actor_id=actor.pk, setting=setting, person_id=person.pk)
        if old:
            return old
        previous = values.filter(setting=setting).order_by("-effective_at", "-pk").first()
        if not query(alias, "SELECT public.r_value_person_allowed(%s,%s,%s,%s,%s)",
                     lo.pk, setting, person.pk, at, at):
            if setting == "CONFIRMER":
                raise ContinuityRefused("The confirmer must be distinct from the founder and both successors.")
            raise ContinuityRefused("The Emergency Steward must be distinct from the founder and the current confirmer.")
        return values.create(organism=lo, setting=setting, person=person, actor=actor,
                             actor_epoch=actor.access_epoch, founder_office_id=office, predecessor=previous,
                             request_uuid=request_uuid, effective_at=at)


def loss_scope(lo, genesis, kind, confirmer_value, beneficiary):
    return Scope(lo.organism_id, confirmer_value.request_uuid, "R:CONFIRM:" + kind,
                 "UNAVAILABILITY_DETERMINATION", genesis.founder_id, beneficiary.pk,
                 lo.founding_authority_basis.basis_uuid)


def confirm_loss(*, credential, organism_uuid, event_kind, finding_uuid, request_uuid, alias="default"):
    require_request(request_uuid)
    if event_kind not in ("INCAPACITY", "DEATH"):
        raise ContinuityRefused("Choose independently established incapacity or death.")
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        value = models.RootValueDecision.objects.using(alias).filter(
            organism=lo, setting="CONFIRMER").order_by("-effective_at", "-pk").first()
        if value is None or value.person_id != actor.pk:
            raise ContinuityRefused("The current named non-beneficiary confirmer is required.")
        person_id = query(alias, "SELECT public.r_successor(%s,%s,%s,%s)", lo.pk, event_kind, at, at)
        evidence = models.GovernedDetermination.objects.using(alias).filter(determination_uuid=finding_uuid).first()
        if evidence is None or person_id is None:
            raise ContinuityRefused("The named successor and an independent finding for this event are required.")
        expected = dict(organism_id=lo.pk, actor_id=actor.pk, event_kind=event_kind, finding_id=evidence.pk)
        old = replay(models.RootLossConfirmation, alias, request_uuid, **expected)
        if old:
            return old
        episode = query(alias, "SELECT public.r_latest_loss(%s,%s,%s)", lo.pk, at, at)
        latest = models.RootLossConfirmation.objects.using(alias).filter(pk=episode).first()
        if latest and (latest.event_kind == "DEATH" or latest.event_kind == event_kind):
            raise ContinuityRefused("This event is already confirmed. A repeated request cannot replace it.")
        if not query(alias, "SELECT public.r_loss_source(%s,%s,%s,%s,%s,%s,%s,%s)",
                     lo.pk, value.pk, event_kind, actor.pk, person_id, evidence.pk, at, at):
            raise ContinuityRefused("The evidence does not independently establish this event for this successor.")
        return models.RootLossConfirmation.objects.using(alias).create(
            **expected, confirmer_value=value, beneficiary_id=person_id, actor_epoch=actor.access_epoch,
            request_uuid=request_uuid, effective_at=at)


def recovery_scope(lo, loss):
    return Scope(lo.organism_id, loss.request_uuid, "R:REGAINED_CAPACITY",
                 "EXECUTION_ELIGIBILITY_QUALIFICATION", lo.founder_identity_id,
                 lo.founder_identity_id, lo.founding_authority_basis.basis_uuid)


def confirm_regained_capacity(*, credential, organism_uuid, loss_uuid, mandate_uuid,
                             verifier_basis, evidence, until, alias="default"):
    """Record the named confirmer's finding under their exact S mandate."""
    from src.intevia.services import scoped_determinations as determinations

    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        episode = query(alias, "SELECT public.r_latest_loss(%s,%s,%s)", lo.pk, at, at)
        loss = models.RootLossConfirmation.objects.using(alias).select_related("finding").filter(
            pk=episode, request_uuid=loss_uuid, organism=lo, event_kind="INCAPACITY", actor=actor).first()
        if loss is None or not query(alias, "SELECT public.r_value_person_allowed(%s,'CONFIRMER',%s,%s,%s)",
                                     lo.pk, actor.pk, at, at):
            raise ContinuityRefused("The current named non-beneficiary confirmer is required for this incapacity.")
        if not isinstance(evidence, str) or not evidence.strip() or evidence.strip() == loss.finding.evidence_reference.strip():
            raise ContinuityRefused("Fresh evidence of regained capacity is required.")
        return determinations.record(alias=alias, scope=recovery_scope(lo, loss), mandate_id=mandate_uuid,
                                     credential=credential, verifier_basis=verifier_basis, result="QUALIFIED",
                                     evidence=evidence, at=at, received=at, until=until)


def restore_founder(*, credential, organism_uuid, loss_uuid, finding_uuid, request_uuid, alias="default"):
    """The Founder's one narrow act consumes an independently recorded, current recovery finding."""
    require_request(request_uuid)
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        if actor.pk != lo.founder_identity_id:
            raise ContinuityRefused("Only the Founder may complete restoration.")
        loss = models.RootLossConfirmation.objects.using(alias).filter(organism=lo, request_uuid=loss_uuid).first()
        finding = models.GovernedDetermination.objects.using(alias).filter(determination_uuid=finding_uuid).first()
        if loss is None or finding is None:
            raise ContinuityRefused("A confirmation of regained capacity for this incapacity is required.")
        expected = dict(loss_id=loss.pk, finding_id=finding.pk, actor_id=actor.pk)
        prior = replay(models.RootCapacityRestoration, alias, request_uuid, **expected)
        if prior:
            return prior
        office = query(alias, "SELECT public.r_restoration_office(%s,%s,%s,%s,%s)",
                       loss.pk, finding.pk, actor.pk, at, at)
        if office is None:
            raise ContinuityRefused("Restoration requires a current, undisputed confirmation of regained capacity.")
        receipt = models.RootCapacityRestoration.objects.using(alias).create(
            **expected, actor_epoch=actor.access_epoch, founder_office_id=office, request_uuid=request_uuid)
        receipt.refresh_from_db(using=alias)
        return receipt


def grant_act(domains, bounds, end):
    if domains not in ("EDUCATION", "LIBRARY", "BOTH"):
        raise ContinuityRefused("Choose the exact EDUCATION or LIBRARY publication scope.")
    if not isinstance(bounds, str) or not bounds.strip() or len(bounds) > 240:
        raise ContinuityRefused("State the necessity and proportionality limits of this grant.")
    stamp = canonical_timestamp(end) if end is not None else "NO_FIXED_END"
    return "R:PUBLISH_OWN:" + domains + ":" + stamp + ":" + sha256(bounds.encode()).hexdigest()


def holder_scope(lo, confirmation, domains, bounds, end):
    return Scope(lo.organism_id, confirmation.request_uuid, grant_act(domains, bounds, end),
                 "EXECUTION_ELIGIBILITY_QUALIFICATION", confirmation.beneficiary_id,
                 confirmation.beneficiary_id, lo.founding_authority_basis.basis_uuid)


def issue_grant(*, credential, organism_uuid, confirmation_uuid, grant_uuid, basis_uuid,
                domains, bounds, hard_expiry, alias="default"):
    """Record the constitutional grant under a root-derived instrument and separate S issuer qualification."""
    require_request(grant_uuid)
    grant_act(domains, bounds, hard_expiry)
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        confirmation = models.RootLossConfirmation.objects.using(alias).filter(
            organism=lo, request_uuid=confirmation_uuid).first()
        basis = models.AuthorityBasis.objects.using(alias).filter(basis_uuid=basis_uuid, issuer=actor).first()
        if confirmation is None or basis is None or confirmation.beneficiary_id == actor.pk:
            raise ContinuityRefused("An independent qualified issuer and their exact constitutional instrument are required.")
        scope = holder_scope(lo, confirmation, domains, bounds, hard_expiry)
        stamp = canonical_timestamp(hard_expiry) if hard_expiry is not None else "NO_FIXED_END"
        method = "|".join(("R:grant:1", str(basis_uuid), str(confirmation.request_uuid), domains,
                           stamp, sha256(bounds.encode()).hexdigest()))
        fingerprint = scope.fingerprint()
        if not query(alias, "SELECT public.r_can_issue(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     confirmation.pk, actor.pk, basis.pk, grant_uuid, fingerprint, domains, stamp,
                     sha256(bounds.encode()).hexdigest(), at, at, actor.access_epoch):
            raise ContinuityRefused("The issuer's current instrument or execution qualification does not cover this grant.")
        prior = models.GovernedDetermination.objects.using(alias).filter(determination_uuid=grant_uuid).first()
        if prior:
            if (prior.determiner_id, prior.method_reference, prior.scope_fingerprint, prior.result,
                prior.tested_actor_id, prior.safeguard_beneficiary_id, prior.determination_type) != (
                actor.pk, method, fingerprint, "QUALIFIED", confirmation.beneficiary_id, confirmation.beneficiary_id,
                "CONSTITUTIONAL_AUTHORITY_QUALIFICATION"):
                raise ContinuityRefused("The grant UUID already identifies different evidence.")
            return prior
        return models.GovernedDetermination.objects.using(alias).create(
            determination_uuid=grant_uuid, determination_type="CONSTITUTIONAL_AUTHORITY_QUALIFICATION",
            determiner=actor, tested_actor_id=confirmation.beneficiary_id,
            safeguard_beneficiary_id=confirmation.beneficiary_id, relied_upon_authority_principal=basis.authority_principal,
            result="QUALIFIED", scope_fingerprint=fingerprint, method_reference=method,
            independence_state="INDEPENDENT", evidence_reference=basis.instrument_reference,
            effective_at=at, occurred_at=at, received_at=at)


def activate(*, credential, organism_uuid, confirmation_uuid, grant_uuid, basis_uuid, finding_uuid,
             domains, bounds, hard_expiry, request_uuid, alias="default"):
    require_request(request_uuid)
    grant_act(domains, bounds, hard_expiry)
    with command_context(alias, credential, organism_uuid) as (lo, actor, at):
        confirmation = models.RootLossConfirmation.objects.using(alias).filter(
            organism=lo, request_uuid=confirmation_uuid, beneficiary=actor).first()
        if confirmation is None:
            raise ContinuityRefused("An independently confirmed event must name this successor.")
        grant = models.GovernedDetermination.objects.using(alias).filter(determination_uuid=grant_uuid).first()
        finding = models.GovernedDetermination.objects.using(alias).filter(determination_uuid=finding_uuid).first()
        basis = models.AuthorityBasis.objects.using(alias).filter(basis_uuid=basis_uuid).first()
        if not all((grant, finding, basis)):
            raise ContinuityRefused("A distinct qualified constitutional grant, basis and holder finding are required.")
        expected = dict(confirmation_id=confirmation.pk, actor_id=actor.pk, grant_id=grant.pk,
                        grant_basis_id=basis.pk, holder_finding_id=finding.pk, domains=domains,
                        bounds=bounds, hard_expiry=hard_expiry)
        old = replay(models.RootContinuityActivation, alias, request_uuid, **expected)
        if old:
            return old
        if not query(alias, "SELECT public.r_grant_ok(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     confirmation.pk, grant.pk, basis.pk, finding.pk, domains, bounds, hard_expiry, at, at):
            raise ContinuityRefused("The current event, holder qualification or exact grant does not authorise activation.")
        code = "INTEVIA_GUIDING_STEWARD" if confirmation.event_kind == "DEATH" else "INTEVIA_EMERGENCY_STEWARD"
        definition = models.OrganismRoleDefinition.objects.using(alias).filter(
            living_organism=lo, code=code, scope="LIVING_ORGANISM", active=True).first()
        if definition is None:
            raise ContinuityRefused("The root lacks the lawful reserved successor office.")
        envelope = None
        if confirmation.event_kind == "INCAPACITY":
            envelope = models.EmergencyAuthorityEnvelope.objects.using(alias).create(
                beneficiary=actor, authority_principal=basis.authority_principal,
                derivation_root_principal=basis.derivation_root_principal, qualifying_constitutional_grant=grant,
                permitted_command_set=["PUBLISH_OWN"], target_scope_set=[str(lo.organism_id), domains],
                proportionality_bounds={"necessity_and_bounds": bounds}, hard_expiry=hard_expiry)
        return models.RootContinuityActivation.objects.using(alias).create(
            **expected, actor_epoch=actor.access_epoch, envelope=envelope, office_definition=definition,
            request_uuid=request_uuid, effective_at=at)


def publication_capacity(alias, lo, actor, domain, at):
    result = query(alias, "SELECT public.r_publication_capacity(%s,%s,%s,%s,%s)",
                   lo.pk, actor.pk, domain, at, at)
    if result is None:
        raise ContinuityRefused("The person holds no effective publication authority for this domain at this time.")
    return result
