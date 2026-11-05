"""R witnesses: issued bytes and supported registration; no root scaffold."""
from pathlib import Path
from uuid import uuid4

from core import models
from src.intevia.services.root_genesis import GenesisAccount, GenesisCommand
from tests import s015_0021_support as fixtures


def command():
    fixtures._require_disposable_fixture_database()
    # Reuse the landed helper to establish labelled terms through supported
    # registration. This preparer's account is not one of the GENESIS accounts.
    fixtures.register_test_learner(username='r-terms-'+uuid4().hex)
    at=fixtures.database_now()
    terms=fixtures.registration_terms_version_in_force(effective_at=at,received_at=at)
    sources=Path(__file__).resolve().parents[1]/'docs/governance/root_genesis'
    tag=uuid4().hex
    return GenesisCommand(organism_uuid=uuid4(),
        founder=GenesisAccount('r-founder-'+tag,'r-alpha-example-password-73',True),
        nominee=GenesisAccount('r-successor-'+tag,'r-alpha-example-password-84',True),
        terms_version_uuid=terms.terms_version_uuid,genesis_terms_accepted=True,
        request_reference='r-genesis:'+tag,idempotency_key=tag,
        principal_invocation=(sources/'PRINCIPAL_INVOCATION_RECEIVED_v1.txt').read_bytes(),
        designation_invocation=(sources/'ROOT_DESIGNATION_INPUT_v1_0.md').read_bytes(),
        adoption_capture=(sources/'HG_RULINGS_R_P3_RD1_RD2_RD3_20261105_CAPTURE.txt').read_bytes())


def counts():
    """No credential values enter an assertion message or evidence snapshot."""
    names=('Identity','LearnerRegistration','RootGenesis','RootSuccessorNomination','AuthorityPrincipal','AuthorityBasis',
           'AuthorityDecision','LivingOrganism','LivingOrganismEvent','LOFoundingContentVersion','PlatformTermsAcceptance',
           'OrganismMembership','OrganismMembershipTransition','OrganismRoleDefinition','OrganismRoleDefinitionProvision',
           'ContextualRoleAssignment','ContextualRoleAssignmentStateEvent','EssentialCoverageRequirement',
           'EssentialCoverageCase','CoverageAssessment')
    from django.contrib.auth.models import User
    return {'credentials':User.objects.count(),**{name:getattr(models,name).objects.count() for name in names}}


def prepare_root_beta(lo,founder):
    """Explicit disposable inputs, using the real gateway, phase, Birthing and SE routes."""
    from hashlib import sha256
    from src.intevia.services.root_circles import RootBirthingInputs,prepare_birthing
    fixtures._require_disposable_fixture_database()
    text='Disposable R rehearsal offering text: explicitly elect the existing complimentary beta package.'
    digest=sha256(text.encode()).hexdigest()
    from django.db.models import Max
    version=(models.OrganismEntitlementPolicyText.objects.aggregate(n=Max('policy_version'))['n'] or 0)+1
    models.OrganismEntitlementPolicyText.objects.create(policy_version=version,accepted_text=text,text_sha256=digest)
    return prepare_birthing(credential=founder.credential,organism_uuid=lo.organism_id,
        inputs=RootBirthingInputs(
            governance=dict(cq_active=False,governance_enabled=False,inherit_portal_cq_settings=False,
                require_cq_to_change_lo_settings=False,require_cq_to_change_application_question=False),
            application_settings=dict(applications_open=True,automatic_acceptance=False,processing_responsibility='LO'),
            application_question=dict(question_text='Why join INTEVIA?',answer_format='LONG_ANSWER'),
            offering_choice='COMPLIMENTARY_BETA',policy_version=version,policy_text_sha256=digest))


def continuity_person():
    return fixtures.register_test_learner(username="r-continuity-" + uuid4().hex)


def external_instrument(scope, verifier=None):
    """Supply the disposable instrument owner input. The application records the finding."""
    from datetime import timedelta
    from tests.test_c2_determinations import ScopedFindingTests
    fixtures._require_disposable_fixture_database()
    instrument = ScopedFindingTests()
    instrument.scope = scope
    instrument.verifier = (verifier or continuity_person()).pk
    instrument.issuer = continuity_person().pk
    instrument.basis = instrument.fresh_basis()
    at = fixtures.database_now()
    instrument.end = at + timedelta(days=7)
    instrument.grant = instrument.mandate(at)
    return instrument


def external_finding(scope, verifier=None):
    return external_instrument(scope, verifier).record()


def root_confirmation(lo, genesis, kind, confirmer):
    from src.intevia.services import root_continuity as continuity
    value = continuity.set_value(credential=genesis.founder.credential, organism_uuid=lo.organism_id,
                                 setting="CONFIRMER", person_uuid=confirmer.identity_id, request_uuid=uuid4())
    fact = external_finding(continuity.loss_scope(lo, genesis, kind, value, genesis.nominee), confirmer)
    return continuity.confirm_loss(credential=confirmer.credential, organism_uuid=lo.organism_id,
                                   event_kind=kind, finding_uuid=fact.determination_uuid, request_uuid=uuid4())


def external_continuity_inputs(lo, confirmation, domains, bounds, end):
    """Supply the two qualification planes through the same owner-input convention as C2 and Circles.

    The external instrument is an input to the actual grant-issue and activation commands.
    Fixture identity or a bare envelope cannot confer the publication function.
    """
    from dataclasses import replace
    from django.db import connection, transaction
    from src.intevia.services import root_continuity as continuity
    fixtures._require_disposable_fixture_database()
    holder_scope = continuity.holder_scope(lo, confirmation, domains, bounds, end)
    holder = external_finding(holder_scope)
    issuer = continuity_person()
    grant_uuid = uuid4()
    issuer_scope = replace(holder_scope, tested_actor_id=issuer.pk,
                           act="R:ISSUE_GRANT:" + str(grant_uuid) + ":" + holder_scope.fingerprint())
    external_finding(issuer_scope)
    with transaction.atomic(), connection.cursor() as cursor:
        _, basis_id = fixtures.seed_authority(cursor, uuid4().hex, issuer.pk,
                                              scope_fingerprint=holder_scope.fingerprint())
    basis = models.AuthorityBasis.objects.get(pk=basis_id)
    models.AuthorityDerivationEdge.objects.create(
        principal=basis.authority_principal, root_principal=lo.founding_authority_basis.authority_principal,
        authority_reference="fixture:root-constitutional-instrument", evidence_reference="fixture:delegation-evidence")
    grant = continuity.issue_grant(credential=issuer.credential, organism_uuid=lo.organism_id,
                                   confirmation_uuid=confirmation.request_uuid, grant_uuid=grant_uuid,
                                   basis_uuid=basis.basis_uuid, domains=domains, bounds=bounds, hard_expiry=end)
    return dict(credential=confirmation.beneficiary.credential, organism_uuid=lo.organism_id,
                confirmation_uuid=confirmation.request_uuid, grant_uuid=grant.determination_uuid,
                basis_uuid=basis.basis_uuid, finding_uuid=holder.determination_uuid, domains=domains,
                bounds=bounds, hard_expiry=end, request_uuid=uuid4())
