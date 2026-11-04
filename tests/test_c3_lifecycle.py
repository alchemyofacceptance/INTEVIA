"""C3 on the run-owned PostgreSQL clone. No wall-clock waits or provider network."""
from datetime import timedelta
from uuid import uuid4

from django.db import connection, transaction
from django.test import TransactionTestCase

from core import models
from core.organism_contract import OrganismAction
from src.intevia.services import authority_lifecycle as life
from src.intevia.services import organism_application_service as commands
from src.intevia.services import scoped_determinations as s
from src.intevia.services.organism_authority import S015Authority
from tests import s015_0021_support as fixtures
from tests import test_sr_office_actions as office_fixtures
from tests.test_c1_rejoin import _attempt
from tests.test_seed_o24_application_terminal_finality import _O24AuthorityProvider


class LifecycleTests(TransactionTestCase):
    def setUp(self):
        self.fx = office_fixtures.SROfficeActionTests()
        self.fx.setUp()
        self.lo = self.fx.organism
        self.holder = self.fx.actor
        self.original_office = models.ContextualRoleAssignment.objects.get(
            membership=self.fx.founder_member, role_definition__code="COORDINATOR")
        self.service = commands.OrganismApplicationService(authority=self.fx.authority, clock=fixtures.database_now)
        self.setting = dict(applications_open=True, automatic_acceptance=False, processing_responsibility="LO")
        self.effect(OrganismAction.EFFECT_APPLICATION_SETTINGS, self.setting)
        self.effect(OrganismAction.EFFECT_APPLICATION_QUESTION, dict(question_text="Why join?", answer_format="LONG_ANSWER"))
        self.issuer = self.identity()
        self.verifier = self.identity()
        self.until = fixtures.database_now() + timedelta(days=1)
        self.claim = self.new_claim()

    def identity(self):
        with transaction.atomic(), connection.cursor() as cursor:
            return models.Identity.objects.get(pk=fixtures.seed_identity(cursor, uuid4().hex))

    def keys(self, actor):
        now = fixtures.database_now()
        return dict(credential=actor.credential, occurred_at=now, effective_at=now,
                    request_reference="c3:" + uuid4().hex, idempotency_key=uuid4().hex)

    def effect(self, action, fields):
        draft = self.service.save_gateway_draft(commands.GatewayDraftCommand(
            **self.keys(self.holder), living_organism_id=self.lo.organism_id, action=action, fields=fields))
        self.service.effect_gateway_version(commands.EffectGatewayVersionCommand(
            **self.keys(self.holder), living_organism_id=self.lo.organism_id, action=action, version_uuid=draft.version_uuid))

    def new_claim(self):
        applicant = fixtures.register_test_learner(username="c3-applicant-" + uuid4().hex)
        # Front-door BIRTHING eligibility is outside this unit. Only SUBMIT uses
        # the existing O-24 provider. Claim, replacement and decision use production.
        submit = commands.OrganismApplicationService(authority=S015Authority(provider=_O24AuthorityProvider()),
                                                      clock=fixtures.database_now)
        app = submit.submit_application(commands.SubmitApplicationCommand(
            **self.keys(applicant), living_organism_id=self.lo.organism_id, answer="Participate"))
        self.service.claim_application(commands.ClaimApplicationCommand(
            **self.keys(self.holder), application_uuid=app.application_uuid))
        return models.CSAAssignment.objects.get(application__application_uuid=app.application_uuid)

    def decide(self, claim, person):
        return self.service.decide_application(commands.DecideApplicationCommand(
            **self.keys(person), application_uuid=claim.application.application_uuid, outcome="DECLINE"))

    def basis(self, scope=None, *, issuer=None):
        with transaction.atomic(), connection.cursor() as cursor:
            _, pk = fixtures.seed_authority(cursor, uuid4().hex, (issuer or self.issuer).pk,
                                           scope_fingerprint=scope.fingerprint() if scope else None)
        return models.AuthorityBasis.objects.get(pk=pk)

    def admission(self, scope, verifier=None):
        verifier = verifier or self.verifier
        basis = self.basis(scope)
        now = fixtures.database_now()
        grant = models.GovernedDetermination.objects.create(
            determination_type=s.Type.CONSTITUTIONAL_AUTHORITY_QUALIFICATION, result="QUALIFIED",
            evidence_reference=basis.instrument_reference, determiner=self.issuer,
            tested_actor=verifier, relied_upon_authority_principal=basis.authority_principal,
            scope_fingerprint=s.mandate_scope(scope, verifier.pk, basis),
            method_reference="S:instrument:1|" + s.canonical_timestamp(self.until),
            independence_state="INDEPENDENT", occurred_at=now, effective_at=now, received_at=now)
        return dict(credential=verifier.credential, mandate_id=grant.determination_uuid,
                    verifier_basis=basis.basis_uuid)

    def finding(self, operation, grant, **kwargs):
        return life.record_case_finding(alias="default", application_uuid=self.claim.application.application_uuid,
            operation=operation, evidence="fixture:recorded-instrument", until=self.until, **grant, **kwargs)

    def aside(self):
        scope = life.scope_for(self.claim, "STEP_ASIDE", self.holder.pk, life._source("default", self.claim).basis_uuid,
                               s.Type.UNAVAILABILITY_DETERMINATION)
        return self.finding("RECUSE", self.admission(scope))

    def nominee(self):
        member, office = self.fx._appoint(self.original_office.role_definition)
        basis = self.basis()
        scope = life.scope_for(self.claim, life._purpose(self.claim, office), member.identity_id, basis.basis_uuid,
                               s.Type.EXECUTION_ELIGIBILITY_QUALIFICATION)
        finding = self.finding("REPLACEMENT", self.admission(scope), office_uuid=office.assignment_uuid,
                               candidate_basis=basis.basis_uuid)
        return member.identity, finding

    def reassign(self, person):
        return self.service.reassign_application(commands.ReassignApplicationCommand(
            **self.keys(person), application_uuid=self.claim.application.application_uuid))

    def test_ended_basis_preserves_prior_decision(self):
        earlier = self.new_claim()
        self.decide(earlier, self.holder)
        history = models.ApplicationDecision.objects.get(application=earlier.application)
        old_ref = history.authority_decision_reference
        source = life._source("default", self.claim)
        separate = self.basis()
        successor = life.SuccessorInstrument(uuid4(), separate.authority_principal.principal_uuid,
            separate.derivation_root_principal.principal_uuid, separate.authority_class, separate.scope_fingerprint,
            "fixture:successor-instrument", "fixture:admitted-terms")
        grant = self.admission(life.end_scope(self.lo.organism_id, source, successor))
        event = life.end_basis(alias="default", organism_uuid=self.lo.organism_id, basis_uuid=source.basis_uuid,
                              evidence="fixture:end-instrument", successor=successor, **grant)
        self.assertTrue(models.AuthorityInvalidationEvent.objects.filter(event_uuid=event, basis=source,
                        kind="SUPERSESSION").exists(), "Ending the old authority must append to its own chain.")
        self.assertTrue(models.AuthorityBasis.objects.filter(basis_uuid=successor.basis_uuid,
                        superseded_basis=source).exists(), "The independently admitted successor must retain its predecessor.")
        now = fixtures.database_now()
        err, _ = _attempt(lambda: life.require_holder("default", self.claim, now, now))
        self.assertIs(type(err), life.CaseHeld, "An open claim must refuse when its office basis has ended.")
        err, _ = _attempt(lambda: self.decide(self.claim, self.holder))
        self.assertIs(type(err), life.CaseHeld, "A later act must report the governed hold for this ended authority.")
        retried = life.end_basis(alias="default", organism_uuid=self.lo.organism_id, basis_uuid=source.basis_uuid,
                                 evidence="fixture:end-instrument", successor=successor, **grant)
        self.assertEqual(retried, event, "Identical evidence must replay the original ending.")
        err, _ = _attempt(lambda: life.end_basis(alias="default", organism_uuid=self.lo.organism_id,
            basis_uuid=source.basis_uuid, evidence="fixture:second-ending", successor=successor, **grant))
        self.assertIs(type(err), s.QualificationRefused, "A different second ending must return the instrument refusal.")
        self.assertIn("its record stands", str(err), "The refusal must explain that the original ending stands.")
        history.refresh_from_db()
        self.assertEqual((history.authority_decision_reference, history.assignment_reference_id), (old_ref, earlier.pk),
                         "The earlier decision must retain its original authority and assignment.")

    def test_lapse_at_exclusive_boundary(self):
        self.aside()
        person, finding = self.nominee()
        self.reassign(person)
        replacement = models.CSAAssignment.objects.get(replaces=self.claim)
        before = self.until - timedelta(microseconds=1)
        # Explicit effective/knowledge cuts of the production DB reader. No clock patch or worker.
        def problem(cut):
            return life.scalar("default", "SELECT public.c3_act_problem(c,%s,%s) FROM public.core_csaassignment c WHERE id=%s",
                               [cut, fixtures.database_now(), replacement.pk])
        self.assertEqual((replacement.lifecycle_finding_id, problem(before), problem(self.until)),
                         (finding.pk, None, "The replacement has no current independent qualification on this case."),
                         "The independent qualification must stop at the instant its instrument expires, even before a worker runs.")

    def test_recusal_is_local_and_derived_reviewer_refuses(self):
        other = self.new_claim()
        marked_under = self.basis(issuer=self.holder)
        step = life.scope_for(self.claim, "STEP_ASIDE", self.holder.pk, marked_under.basis_uuid,
                              s.Type.UNAVAILABILITY_DETERMINATION)
        self.finding("RECUSE", self.admission(step), candidate_basis=marked_under.basis_uuid)
        err, _ = _attempt(lambda: self.decide(self.claim, self.holder))
        self.assertIs(type(err), life.CaseHeld, "The recused holder must receive the case-specific hold.")
        self.decide(other, self.holder)
        self.assertTrue(models.ApplicationDecision.objects.filter(application=other.application).exists(),
                        "The same holder must remain able to decide an unrelated case.")
        subject = life._source("default", self.claim)
        scope = life.scope_for(self.claim, f"REVIEW:{self.claim.assignment_uuid}", self.holder.pk, subject.basis_uuid,
                               s.Type.INDEPENDENCE_DETERMINATION)
        grant = self.admission(scope)
        basis = models.AuthorityBasis.objects.get(basis_uuid=grant["verifier_basis"])
        models.AuthorityDerivationEdge.objects.create(principal=basis.authority_principal,
            root_principal=subject.authority_principal, authority_reference="fixture:common-root",
            evidence_reference="fixture:derivation")
        err, _ = _attempt(lambda: self.finding("PROCEDURAL_REVIEW", grant))
        self.assertIs(type(err), s.QualificationRefused, "A reviewer with the same underlying authority must be refused.")
        self.assertIn("same source", str(err), "The refusal must identify the shared source.")

        def available_at(cut):
            return life.scalar("default", "SELECT public.c3_available(%s,%s,%s,%s)",
                               [self.claim.application_id, self.holder.pk, cut, fixtures.database_now()])

        self.assertFalse(available_at(self.until), "Expiry alone must not clear a recorded conflict.")
        ending = self.admission(life.end_scope(self.lo.organism_id, marked_under))
        life.end_basis(alias="default", organism_uuid=self.lo.organism_id, basis_uuid=marked_under.basis_uuid,
                       evidence="fixture:end-step-source", **ending)
        self.assertFalse(available_at(fixtures.database_now()), "Ending the old basis must not release the holder.")
        live = self.basis(issuer=self.holder)
        release = life.scope_for(self.claim, "STEP_ASIDE", self.holder.pk, live.basis_uuid,
                                 s.Type.UNAVAILABILITY_DETERMINATION)
        released = self.finding("AVAILABLE", self.admission(release), candidate_basis=live.basis_uuid)
        self.assertEqual(released.result, "REFUSED", "The independent verifier must record the release through S.")
        self.assertTrue(available_at(fixtures.database_now()), "A live instrument must clear the step-aside on the ended basis.")
        self.assertTrue(available_at(self.until), "An admitted release must survive its instrument's expiry.")
        ending = self.admission(life.end_scope(self.lo.organism_id, live))
        life.end_basis(alias="default", organism_uuid=self.lo.organism_id, basis_uuid=live.basis_uuid,
                       evidence="fixture:end-release-source", **ending)
        self.assertTrue(available_at(fixtures.database_now()), "Ending the release basis must not revive an old conflict.")
        self.fx._run(lambda: self.decide(self.claim, self.holder), "Deciding after a lawful cross-basis release")

    def test_replacement_retains_original_rule_after_change(self):
        original = life.first_engagement(self.claim.application)
        self.aside()
        self.effect(OrganismAction.EFFECT_APPLICATION_SETTINGS, self.setting | {"applications_open": False})
        person, finding = self.nominee()
        self.fx._run(lambda: self.reassign(person), "Taking over under the original settings after they changed")
        replacement = models.CSAAssignment.objects.get(replaces=self.claim)
        self.fx._run(lambda: self.decide(replacement, person), "Deciding under the successor's own fresh authority")
        last = models.ApplicationEngagement.objects.get(application=self.claim.application, engagement_ordinal=2)
        decision = models.ApplicationDecision.objects.get(application=self.claim.application)
        self.assertEqual((last.settings_basis, replacement.lifecycle_finding_id, decision.assignment_reference_id,
                          decision.responsible_coordinator_id), (original.settings_basis, finding.pk, replacement.pk, person.pk),
                         "The successor must decide on their own qualification under the original pair of governing versions.")

    def test_no_replacement_preserves_work_and_routes_hold(self):
        self.aside()
        member, _ = self.fx._appoint(self.original_office.role_definition)
        err, _ = _attempt(lambda: self.reassign(member.identity))
        self.assertIs(type(err), life.CaseHeld, "A Coordinator without a fresh finding must receive a governed hold.")
        self.assertEqual((err.result.route, err.result.application_uuid, err.result.rule),
                         ("EXISTING_QUALIFIED_GOVERNANCE", self.claim.application.application_uuid,
                          life.first_engagement(self.claim.application).settings_basis),
                         "The hold must identify the existing governance route, preserved application and governing versions.")
        self.assertFalse(models.CSAAssignmentEnd.objects.filter(assignment=self.claim).exists(),
                         "A refused transfer must leave the existing assignment intact.")
        self.assertFalse(models.ApplicationDecision.objects.filter(application=self.claim.application).exists(),
                         "The applicant must retain an open case.")

        from src.intevia.services.organism_application_reads import OrganismApplicationReader
        reader = OrganismApplicationReader(authority=self.fx.authority, clock=fixtures.database_now)
        visible = reader.read_authority_hold(self.claim.application.application_uuid, self.claim.application.applicant)
        queue = reader.governance_holds(self.lo.pk, member.identity)
        self.assertIsNotNone(visible, "A held case must be visible to its waiting applicant.")
        self.assertEqual((visible.organism_uuid, [row.application_uuid for row in queue]),
                         (self.lo.organism_id, [self.claim.application.application_uuid]),
                         "The waiting applicant can see where the case went and qualified governance can find it.")

    def test_title_cannot_supply_procedural_mandate(self):
        self.fx._custom_entitlement()
        member, _ = self.fx._appoint(self.original_office.role_definition)
        subject = life._source("default", self.claim)
        scope = life.scope_for(self.claim, f"REVIEW:{self.claim.assignment_uuid}", self.holder.pk,
                               subject.basis_uuid, s.Type.INDEPENDENCE_DETERMINATION)
        grant = self.admission(scope, member.identity)
        err, _ = _attempt(lambda: self.finding("PROCEDURAL_REVIEW", grant | {"mandate_id": uuid4()}))
        self.assertIs(type(err), s.QualificationRefused, "An occupied office gives no case review power without its mandate.")
        self.assertFalse(models.GovernedDetermination.objects.filter(scope_fingerprint=scope.fingerprint()).exists(),
                         "The unmandated reviewer must leave no procedural finding.")
        result = self.finding("PROCEDURAL_REVIEW", grant)
        self.assertEqual((result.determiner_id, result.scope_fingerprint, result.result),
                         (member.identity_id, scope.fingerprint(), "QUALIFIED"),
                         "The independent, expressly mandated reviewer must leave a finding on the exact case.")
        self.assertFalse(models.ApplicationDecision.objects.filter(application=self.claim.application).exists(),
                         "A procedural finding must leave the substantive decision with its decider.")
        review_basis = models.AuthorityBasis.objects.get(basis_uuid=grant["verifier_basis"])
        aside = life.scope_for(self.claim, "STEP_ASIDE", member.identity_id, review_basis.basis_uuid,
                               s.Type.UNAVAILABILITY_DETERMINATION)
        self.finding("UNAVAILABLE", self.admission(aside), tested_actor_id=member.identity_id,
                     candidate_basis=review_basis.basis_uuid)
        err, _ = _attempt(lambda: self.finding("PROCEDURAL_REVIEW", grant))
        self.assertIs(type(err), life.CaseHeld, "An unavailable reviewer must step aside on this case even with a mandate.")
        self.assertTrue(models.GovernedDetermination.objects.filter(pk=result.pk, result="QUALIFIED").exists(),
                        "The earlier procedural finding must retain its recorded outcome.")
