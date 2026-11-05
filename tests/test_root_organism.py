"""R-01 to R-07: governed GENESIS, Circles, publication boundary and root continuity.

Seven proposed product labels cumulatively. R-04 covers the declaration and publication refusals. No standing grant, disabled guardian,
pre-existing root scaffold or independent-qualification claim is used to found
this root. The one late invalid envelope is an input refusal, not a new recurring
mutation control. PostgreSQL execution belongs to the VC.
"""
from dataclasses import replace
from hashlib import sha256
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models, root_genesis_contract as contract
from src.intevia.services import root_genesis as genesis
from src.intevia.services import organism_role_definition_service as definitions
from src.intevia.services import organism_role_service as roles
from tests import root_genesis_support as support
from tests import s015_0021_support as fixtures
from tests.test_c1_rejoin import _attempt
from tests.test_sr_office_guards import _add_member, _anchor, _propose


class RootOrganismTests(TransactionTestCase):
    def guards(self):
        with connection.cursor() as cursor:
            state=fixtures.root_scaffold_state(cursor)
            self.assertFalse(state['unset'],'GENESIS must use the replacement safeguards, not the old prohibition.')
            self.assertEqual(len(state['guards']),3,'All replacement root insertion guards must be installed.')
            self.assertTrue(all(mode=='O' for _,_,mode in state['guards']), 'No root guard may be disabled for this witness.')

    def fold(self, token, pk):
        at=fixtures.database_now()
        with connection.cursor() as cursor:
            cursor.execute('SELECT public.s015_fold_effective_state(%s,%s,%s,%s)',[token,pk,at,at])
            return cursor.fetchone()[0]

    def successful(self, command):
        error,lo=_attempt(lambda:genesis.execute(command))
        self.assertIsNone(error,'The issued GENESIS must commit through the real route; actual failure type: '+type(error).__name__)
        return lo

    def test_genesis_root_offices_and_accounts(self):
        """R-01 / FW-11 positive: real founding with no supplied standing grant."""
        command=support.command(); before=support.counts(); self.guards()
        lo=self.successful(command)
        g=models.RootGenesis.objects.get(organism_uuid=lo.organism_id)
        from core.organism_contract import ORIGINATING_INTEVIA_LO_REFERENCE
        self.assertEqual(g.canonical_reference, 'intevia-root-lo')
        self.assertEqual(g.canonical_reference, ORIGINATING_INTEVIA_LO_REFERENCE)
        self.assertEqual((lo.platform_root,lo.state),(True,'ACTIVE'))
        self.assertEqual(self.fold('core_livingorganism',lo.pk),'ACTIVE')
        self.assertEqual(sha256(bytes(g.principal_invocation)).hexdigest(),contract.PRINCIPAL_SHA256)
        self.assertEqual(sha256(bytes(g.designation_invocation)).hexdigest(),contract.DESIGNATION_SHA256)
        self.assertEqual(sha256(bytes(g.adoption_capture)).hexdigest(),contract.ADOPTION_SHA256)
        self.assertEqual(lo.founder_identity_id,g.founder_id)
        self.assertEqual(g.founder.display_name,'Carmian Owen')
        self.assertEqual(g.nominee.display_name,'Debra Reyes')
        after=support.counts()
        self.assertEqual(after['credentials']-before['credentials'],2,'GENESIS must create exactly its two supplied accounts.')
        self.assertEqual(after['LearnerRegistration']-before['LearnerRegistration'],2)
        inventory=set(models.OrganismRoleDefinition.objects.filter(living_organism=lo,active=True).values_list('code',flat=True))
        self.assertEqual(inventory,set(contract.RESERVED)|{'COORDINATOR'})
        member=models.OrganismMembership.objects.get(living_organism=lo,identity=g.founder)
        self.assertEqual(self.fold('core_organismmembership',member.pk),'ACTIVE')
        offices=models.ContextualRoleAssignment.objects.filter(membership=member)
        self.assertEqual(set(offices.values_list('role_definition__code',flat=True)),set(contract.FOUNDER_OFFICES))
        for office in offices:
            self.assertEqual(self.fold('core_contextualroleassignment',office.pk),'ACTIVE',f'{office.role_definition.code} must be effective.')
            self.assertEqual(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=office).count(),1)
        self.assertEqual(set(g.nominations.values_list('definition__code',flat=True)),set(contract.SUCCESSOR_OFFICES))
        self.assertTrue(all(n.nominee_id==g.nominee_id for n in g.nominations.all()))
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(membership__identity=g.nominee).exists(),
                         'A nominated successor must hold no active office merely because GENESIS names them.')
        self.assertEqual(models.GovernedDetermination.objects.count(),0,'The positive route must not depend on a pre-supplied qualification grant.')
        self.assertEqual(models.Circle.objects.filter(parent_organism=lo).count(),0,'P1 must not silently perform P2 Circle birth.')
        self.guards()

    def test_genesis_and_reserved_route_boundaries(self):
        """R-02: late failure rollback, singleton, ordinary routes and holder protection."""
        command=support.command(); self.guards(); before=support.counts()
        original=genesis._append; reached=[]
        def invalid_assessment(anchor,*args,**kwargs):
            if isinstance(anchor,models.EssentialCoverageCase):
                reached.append(True)
                args=list(args); args[7]='DEFICIT'  # outcome after action; rejected by the real writer
                return original(anchor,*args,**kwargs)
            return original(anchor,*args,**kwargs)
        with patch.object(genesis,'_append',side_effect=invalid_assessment):
            error,_=_attempt(lambda:genesis.execute(command))
        self.assertTrue(reached,'The rollback witness must reach the late coverage write after accounts and offices exist.')
        self.assertIsInstance(error,DatabaseError,'Invalid GENESIS coverage must be refused by the database.')
        self.assertIn('R_GENESIS_WRITER_SOURCE',str(error))
        self.assertEqual(support.counts(),before,'A late refused GENESIS must leave no account, designation, office or root behind.')
        undeclared_uuid=uuid4(); at=fixtures.database_now()
        with self.assertRaisesRegex(IntegrityError,'R_GENESIS_DESIGNATION_REQUIRED'):
            with transaction.atomic():
                models.LivingOrganism.objects.create(organism_id=undeclared_uuid,slug='undeclared-root',platform_root=True,
                    state='FOUNDING_PENDING',constitutional_spine_reference='unissued-root-designation',
                    **genesis._cache('core_livingorganism',undeclared_uuid,at))
        self.assertEqual(support.counts(),before,'A root without its designation must leave nothing behind.')
        lo=self.successful(command); g=models.RootGenesis.objects.get(organism_uuid=lo.organism_id)
        after=support.counts()
        for candidate in (command,replace(command,organism_uuid=uuid4(),idempotency_key=uuid4().hex)):
            with self.assertRaisesRegex(genesis.GenesisRefused,'already exists'):
                genesis.execute(candidate)
        self.assertEqual(support.counts(),after,'No repeat may create a second root or account batch.')
        with self.assertRaisesRegex(genesis.GenesisRefused,'invocation bytes'):
            genesis.execute(replace(command,principal_invocation=command.principal_invocation+b' '))
        member=models.OrganismMembership.objects.get(living_organism=lo,identity=g.founder)
        role=models.OrganismRoleDefinition.objects.get(living_organism=lo,code='INTEVIA_STEWARD')
        at=fixtures.database_now(); ids={'lo':lo.pk,'basis':lo.founding_authority_basis_id,'identity':g.founder_id,'membership':member.pk}
        with transaction.atomic(),connection.cursor() as cursor:
            another,_,another_uuid=_add_member(cursor,ids,'r-holder-'+uuid4().hex,at)
        # The provider checks the reserved office before the assignment service's local rule.
        from src.intevia.services.organism_authority import S015Authority, S015AuthorityDenied, S015AuthorityRefusalCode
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        service=roles.OrganismRoleService(authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),clock=fixtures.database_now)
        proposed=roles.ProposeAssignmentCommand(credential=g.founder.credential,request_reference='r-propose',idempotency_key=uuid4().hex,
            occurred_at=at,effective_at=at,membership_uuid=another_uuid,role_definition_uuid=role.role_uuid,
            effective_until_state='NONE',effective_until=None)
        error,_=_attempt(lambda:service.propose_assignment(proposed))
        self.assertIsNotNone(error,'An ordinary nomination must not fill a reserved office.')
        self.assertIsInstance(error, S015AuthorityDenied)
        self.assertEqual(error.refusal_code, S015AuthorityRefusalCode.C_RESERVED_ROOT_OFFICE)
        founder_office=models.ContextualRoleAssignment.objects.get(membership=member,role_definition__code='INTEVIA_FOUNDER_STEWARD')
        for action,outcome in (('ASSIGN','ACTIVE'),('END','ENDED'),('REASSIGN','ENDED')):
            cmd=roles.TransitionAssignmentCommand(credential=g.founder.credential,
                request_reference='r-ordinary-'+action,idempotency_key=uuid4().hex,occurred_at=at,effective_at=at,
                assignment_uuid=founder_office.assignment_uuid,action=action,resulting_state=outcome,
                effective_until_state='NONE' if action=='ASSIGN' else None)
            error,_=_attempt(lambda:service.transition_assignment(cmd))
            self.assertIsNotNone(error,action+' must not change a reserved founder office.')
            self.assertIsInstance(error, S015AuthorityDenied, action)
            self.assertEqual(error.refusal_code, S015AuthorityRefusalCode.C_RESERVED_ROOT_OFFICE, action)
        definition_service=definitions.OrganismRoleDefinitionService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),clock=fixtures.database_now)
        content=definitions.DefinitionContent(code='INTEVIA_STEWARD')
        for action in ('DEFINE','AMEND','RETIRE'):
            cmd=definitions.RecordRoleDefinitionCommand(credential=g.founder.credential,living_organism_uuid=lo.organism_id,
                event_action=action,request_reference='r-definition-'+action,idempotency_key=uuid4().hex,
                occurred_at=at,effective_at=at,content=content if action!='RETIRE' else None,
                role_definition_uuid=role.role_uuid if action!='DEFINE' else None,
                retirement_reason='Reserved route must refuse' if action=='RETIRE' else None)
            error,_=_attempt(lambda:definition_service.record_role_definition(cmd))
            self.assertIsInstance(error,definitions.S015RoleDefinitionRefused)
            self.assertEqual(error.code,'SR_ROOT_OFFICE_RESERVED',action+' must preserve the reserved definition boundary.')
        self.assertEqual(self.fold('core_contextualroleassignment',founder_office.pk),'ACTIVE')
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                models.LivingOrganism.objects.filter(pk=lo.pk).update(platform_root=False)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                models.LivingOrganism.objects.filter(pk=lo.pk).update(founder_identity=g.nominee)
        # Direct nomination still reaches the unchanged database guard, not merely the service gate.
        with self.assertRaisesRegex(IntegrityError,'reserved root office'):
            with transaction.atomic(),connection.cursor() as cursor:
                _propose(cursor,ids,proposer_id=g.founder_id,proposer_membership_id=member.pk,
                    nominee_membership_id=another,nominee_membership_uuid=another_uuid,definition_id=role.pk,
                    definition_uuid=role.role_uuid,at=at,tag='r-direct-'+uuid4().hex)
        founder_role=models.OrganismRoleDefinition.objects.get(living_organism=lo,code='INTEVIA_FOUNDER_STEWARD')
        with self.assertRaisesRegex(IntegrityError,'founder identity'):
            with transaction.atomic(),connection.cursor() as cursor:
                _anchor(cursor,another,founder_role.pk,at)
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        with self.assertRaisesRegex(IntegrityError,'reserved root office'):
            with transaction.atomic():
                models.OrganismRoleDefinition.objects.filter(pk=role.pk).update(active=False)
        # Ordinary LO placement must refuse even a copy of a genuine GENESIS definition.
        # The ordinary founding follows GENESIS under the fixture terms in force at that time.
        ordinary_at = fixtures.database_now()
        with transaction.atomic(), connection.cursor() as cursor:
            ordinary = fixtures.found_nonroot(cursor, tag="r-ordinary-" + uuid4().hex, fixture_at=ordinary_at)
        ordinary_lo = models.LivingOrganism.objects.get(pk=ordinary["lo"])
        self.assertTrue(models.PlatformTermsAcceptance.objects.filter(
            acceptance_kind="FOUNDING", subject_reference=f"living-organism:{ordinary_lo.organism_id}",
            accepted_by_identity_id=ordinary["identity"], accepted_at=ordinary_at,
            terms_version__effective_from__lte=ordinary_at,
        ).exists(), "The ordinary LO must commit with its founder’s acceptance of terms effective at founding.")
        with self.assertRaises(DatabaseError):
            with transaction.atomic(),connection.cursor() as cursor:
                cursor.execute('''INSERT INTO public.core_organismroledefinition
                    (role_uuid,living_organism_id,code,scope,definition_version,active,created_at,kind,appointing_authority,
                     appointing_body_reference,nomination_basis,nomination_policy,nomination_dependency,participation_required,
                     enterer_id,enterer_access_epoch,authority_decision_reference,occurred_at,effective_at,received_at)
                    SELECT %s,%s,code,scope,1,true,statement_timestamp(),kind,appointing_authority,appointing_body_reference,
                     nomination_basis,nomination_policy,nomination_dependency,participation_required,enterer_id,enterer_access_epoch,
                     authority_decision_reference,occurred_at,effective_at,received_at FROM public.core_organismroledefinition WHERE id=%s''',
                    [uuid4(),ordinary['lo'],role.pk])
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        self.assertFalse(models.OrganismRoleDefinition.objects.filter(living_organism_id=ordinary['lo'],code__startswith='INTEVIA_').exists())
        self.assertEqual(lo.founder_identity_id,g.founder_id)
        self.guards()

    def test_ten_root_circles_use_governed_birth(self):
        """R-03: actual SE election and ten ordinary qualified births; no root fixture or grant."""
        from src.intevia.services import root_circles, circle_commands
        from src.intevia.services.organism_entitlement_service import check_allowance
        lo=self.successful(support.command());g=models.RootGenesis.objects.get(organism_uuid=lo.organism_id)
        args=dict(credential=g.founder.credential,organism_uuid=lo.organism_id,request_uuid=uuid4())
        with transaction.atomic(), self.assertRaisesRegex(circle_commands.CircleAccessRefused, "outside an existing transaction"):
            root_circles.birth_inventory(**args)
        with self.assertRaisesRegex(circle_commands.CircleAccessRefused,'Birthing'):
            root_circles.birth_inventory(**args)
        error,result=_attempt(lambda:support.prepare_root_beta(lo,g.founder))
        self.assertIsNone(error,'Actual root Birthing and SE election must complete; failure type: '+type(error).__name__)
        self.assertIsNotNone(result.entitlement_uuid,'The real beta election must compose its own grant.')
        self.assertEqual(check_allowance(lo.organism_id, 'circles', 10, fixtures.database_now()).status, 'within')
        before=models.ContextualRoleAssignment.objects.count();original=circle_commands.form;reached=[]
        def bad_last(**kwargs):
            reached.append(kwargs['name'])
            if len(reached)==10:kwargs['name']=''
            return original(**kwargs)
        with patch.object(circle_commands,'form',side_effect=bad_last):
            error,_=_attempt(lambda:root_circles.birth_inventory(**args))
        self.assertEqual(len(reached),10,'The negative batch must reach its tenth ordinary birth.')
        self.assertIsInstance(error,ValueError,'A blank final Circle name must refuse.')
        self.assertFalse(models.Circle.objects.filter(parent_organism=lo).exists(),'All nine earlier births must roll back with the last refusal.')
        self.assertFalse(models.CircleBirthDescription.objects.exists())
        self.assertFalse(models.RootCircleBatch.objects.exists())
        self.assertEqual(models.ContextualRoleAssignment.objects.count(),before)
        error,batch=_attempt(lambda:root_circles.birth_inventory(**args))
        self.assertIsNone(error,'Ten Circles must complete through actual birth; failure type: '+type(error).__name__)
        self.assertEqual(len(batch.circles),10)
        self.assertEqual(root_circles.birth_inventory(**args).pk,batch.pk,'Exact replay must not create more Circles.')
        descriptions=models.CircleBirthDescription.objects.filter(circle__parent_organism=lo).select_related('circle')
        self.assertEqual({(d.name,d.purpose) for d in descriptions},set(root_circles.INVENTORY))
        self.assertEqual(models.Circle.objects.filter(parent_organism=lo).count(),10)
        for d in descriptions:
            self.assertEqual((d.circle.kind,self.fold('core_circle',d.circle_id)),('GENERIC','ACTIVE'))
            with connection.cursor() as cursor:
                now=fixtures.database_now()
                cursor.execute('SELECT person FROM public.circle_holders(%s,%s,%s)',[d.circle_id,now,now])
                self.assertEqual(cursor.fetchall(),[(g.founder_id,)],'Each actual Circle needs its founder as qualified Coordinator.')
            self.assertEqual(models.CircleParticipation.objects.get(circle=d.circle).membership.identity_id,g.founder_id)
        self.assertFalse(descriptions.filter(name='INTEVIA Community').exists())
        self.assertFalse(models.Circle.objects.filter(parent_organism=lo,kind__in=('GOVERNANCE','OPERATIONS','FINANCE')).exists())
        self.guards()

    def test_publication_requires_declaration_and_exact_authorship(self):
        """R-04 PARTIAL: explicit declaration and closed missing-interface boundary, not a publication pass."""
        from src.intevia.services import root_publication as publication
        from core.root_publication_contract import DECLARATION_TEXT,SOURCE_HASHES
        lo=self.successful(support.command());g=models.RootGenesis.objects.get(organism_uuid=lo.organism_id)
        args=dict(credential=g.founder.credential,organism_uuid=lo.organism_id)
        self.assertFalse(models.RootPublicationDeclaration.objects.exists(),'GENESIS must not silently declare the exception.')
        error,_=_attempt(lambda:publication.publish_own(**args,domain='LIBRARY',version_id=-1))
        self.assertIsInstance(error,publication.PublicationRefused)
        self.assertEqual(error.code,'R_DECLARATION_REQUIRED')
        key=uuid4()
        with self.assertRaises(publication.PublicationRefused):
            publication.declare(**args,request_uuid=key,accept_declaration=False,access_posture='PRIVATE_DECLARING_ACTOR')
        error,decision=_attempt(lambda:publication.declare(**args,request_uuid=key,accept_declaration=True,access_posture='PRIVATE_DECLARING_ACTOR'))
        self.assertIsNone(error,'The deliberate Founder declaration must be recordable; failure type: '+type(error).__name__)
        self.assertEqual((decision.text,decision.source_hashes),(DECLARATION_TEXT,SOURCE_HASHES))
        self.assertEqual(decision.decision_kind,'PUBLICATION_EXCEPTION')
        self.assertEqual(decision.record_class,'DECISION')
        self.assertEqual(decision.access_posture,'PRIVATE_DECLARING_ACTOR')
        self.assertEqual(decision.prior_root_event.living_organism_id,lo.pk)
        self.assertEqual(publication.declare(**args,request_uuid=key,accept_declaration=True,access_posture='PRIVATE_DECLARING_ACTOR').pk,decision.pk)
        error,_=_attempt(lambda:publication.publish_own(**args,domain='LIBRARY',version_id=-1))
        self.assertIsInstance(error,publication.PublicationRefused)
        self.assertEqual(error.code,'R_EXACT_VERSION_REQUIRED')
        error,_=_attempt(lambda:publication.publish_own(**args,domain='EDUCATION',version_id=-1))
        self.assertIsInstance(error,publication.PublicationRefused)
        self.assertEqual(error.code,'R_COURSE_RELEASE_INTERFACE_MISSING','A CourseVersion must never stand in for the missing exact release.')
        # An invalid direct-write declaration must reach the database guard, not model uniqueness validation.
        row={f.attname:getattr(decision,f.attname) for f in decision._meta.concrete_fields if not f.primary_key}
        row.update(declaration_uuid=uuid4(),request_uuid=uuid4(),actor_id=g.nominee_id,
                   actor_access_epoch=g.nominee.access_epoch,recorded_at=None)
        with self.assertRaisesRegex(IntegrityError,'R_DECLARATION_SOURCE'):
            with transaction.atomic():
                models.RootPublicationDeclaration.objects.bulk_create([models.RootPublicationDeclaration(**row)])
        with self.assertRaises(publication.PublicationRefused):
            publication.publish_own(**args,domain='DISCUSSION',version_id=-1)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():models.RootPublicationDeclaration.objects.filter(pk=decision.pk).update(text='changed')
        self.assertEqual(models.RootPublicationDeclaration.objects.count(),1)
        self.guards()

    def test_nomination_and_death_succession_authority(self):
        """R-05. A nominated successor needs a confirmed death and an independently qualified grant."""
        from src.intevia.services import root_continuity as r, root_publication as publication
        lo = self.successful(support.command())
        genesis = models.RootGenesis.objects.get(organism_uuid=lo.organism_id)
        def capacity(person):
            at = fixtures.database_now()
            return r.query("default", "SELECT public.r_publication_capacity(%s,%s,%s,%s,%s)",
                           lo.pk, person.pk, "LIBRARY", at, at)
        self.assertIsNone(capacity(genesis.nominee), "A nomination alone must confer no publication authority.")
        publication.declare(credential=genesis.founder.credential, organism_uuid=lo.organism_id,
                            request_uuid=uuid4(), accept_declaration=True, access_posture="PRIVATE_DECLARING_ACTOR")
        confirmation = support.root_confirmation(lo, genesis, "DEATH", support.continuity_person())
        self.assertIsNone(capacity(genesis.nominee), "Independent confirmation alone must grant no successor authority.")
        args = support.external_continuity_inputs(lo, confirmation, "LIBRARY", "Library publication only", None)
        with self.assertRaisesRegex(r.ContinuityRefused, "independent qualified issuer"):
            r.issue_grant(credential=genesis.nominee.credential, organism_uuid=lo.organism_id,
                          confirmation_uuid=confirmation.request_uuid, grant_uuid=uuid4(),
                          basis_uuid=args["basis_uuid"], domains="LIBRARY", bounds=args["bounds"], hard_expiry=None)
        activation = r.activate(**args)
        self.assertEqual(capacity(genesis.nominee), "ACTIVATION:" + str(activation.request_uuid))
        self.assertEqual(r.activate(**args).pk, activation.pk, "An exact retry must return the existing grant exercise.")
        with self.assertRaises(publication.PublicationRefused) as unavailable:
            publication.publish_own(credential=genesis.nominee.credential, organism_uuid=lo.organism_id,
                                    domain="LIBRARY", version_id=-1)
        self.assertEqual(unavailable.exception.code, "R_EXACT_VERSION_REQUIRED",
                         "A qualified successor must reach the same exact-version boundary as the Founder.")
        self.assertIsNone(capacity(genesis.founder), "The deceased Founder cannot exercise the function.")
        lo.refresh_from_db()
        self.assertEqual(lo.founder_identity_id, genesis.founder_id)
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(
            membership__identity=genesis.nominee, role_definition__code="INTEVIA_FOUNDER_STEWARD").exists())
        self.assertEqual(activation.office_definition.code, "INTEVIA_GUIDING_STEWARD")
        self.assertIsNone(activation.envelope_id)

        # A new positive finding for the very same scope must not extend an older activation.
        scope = r.holder_scope(lo, confirmation, args["domains"], args["bounds"], args["hard_expiry"])
        replacement = support.external_finding(scope)
        cut = fixtures.database_now()
        current = r.query("default", "SELECT public.r_holder_finding(%s,%s,%s,%s,%s,%s)",
                          confirmation.pk, args["domains"], args["bounds"], args["hard_expiry"], cut, cut)
        self.assertEqual(current, replacement.pk, "The replacement must itself be the current qualified finding.")
        self.assertNotEqual(current, activation.holder_finding_id)
        with self.assertRaises(publication.PublicationRefused) as lapsed:
            publication.publish_own(credential=genesis.nominee.credential, organism_uuid=lo.organism_id,
                                    domain="LIBRARY", version_id=-1)
        self.assertEqual(lapsed.exception.code, "R_EFFECTIVE_AUTHORITY_REQUIRED",
                         "Publication must refuse once the finding bound to this activation is replaced.")
        self.guards()

    def test_emergency_confirmation_expiry_and_restoration(self):
        """R-06. Test RC-2, the bounded emergency grant and independently confirmed restoration."""
        from datetime import timedelta
        from src.intevia.services import root_continuity as r
        lo = self.successful(support.command())
        genesis = models.RootGenesis.objects.get(organism_uuid=lo.organism_id)
        acting = dict(credential=genesis.nominee.credential, organism_uuid=lo.organism_id)
        with self.assertRaisesRegex(r.ContinuityRefused, "non-beneficiary"):
            r.confirm_loss(**acting, event_kind="INCAPACITY", finding_uuid=uuid4(), request_uuid=uuid4())
        with self.assertRaisesRegex(r.ContinuityRefused, "distinct"):
            r.set_value(credential=genesis.founder.credential, organism_uuid=lo.organism_id, setting="CONFIRMER",
                        person_uuid=genesis.nominee.identity_id, request_uuid=uuid4())
        confirmer = support.continuity_person()
        confirmation = support.root_confirmation(lo, genesis, "INCAPACITY", confirmer)
        end = fixtures.database_now() + timedelta(hours=2)  # A case input on the disposable database.
        args = support.external_continuity_inputs(lo, confirmation, "EDUCATION", "Publish own course material", end)
        with self.assertRaisesRegex(r.ContinuityRefused, "grant"):
            r.activate(**(args | {"domains": "BOTH"}))
        self.assertFalse(models.EmergencyAuthorityEnvelope.objects.exists())
        activation = r.activate(**args)
        self.assertEqual(activation.office_definition.code, "INTEVIA_EMERGENCY_STEWARD")
        self.assertEqual(activation.envelope.hard_expiry, end)
        self.assertEqual(activation.envelope.qualifying_constitutional_grant_id, activation.grant_id)
        def capacity(person, domain, cut):
            return r.query("default", "SELECT public.r_publication_capacity(%s,%s,%s,%s,%s)",
                           lo.pk, person.pk, domain, cut, cut)
        now = fixtures.database_now()
        self.assertEqual(capacity(genesis.nominee, "EDUCATION", now), "ACTIVATION:" + str(activation.request_uuid))
        self.assertIsNone(capacity(genesis.nominee, "LIBRARY", now), "A grant cannot spill into another domain.")
        self.assertIsNone(capacity(genesis.nominee, "EDUCATION", end), "Hard expiry must refuse at the exact boundary.")
        self.assertIsNone(capacity(genesis.founder, "EDUCATION", end),
                          "Expiry alone cannot restore the Founder. A fresh governed restoration is required.")
        with self.assertRaisesRegex(r.ContinuityRefused, "Founder Steward"):
            r.set_value(credential=genesis.founder.credential, organism_uuid=lo.organism_id, setting="CONFIRMER",
                        person_uuid=genesis.nominee.identity_id, request_uuid=uuid4())
        from src.intevia.services import scoped_determinations as determinations
        scope = r.recovery_scope(lo, confirmation)
        instrument = support.external_instrument(scope, confirmer)
        confirm = dict(organism_uuid=lo.organism_id, loss_uuid=confirmation.request_uuid,
                       mandate_uuid=instrument.grant.determination_uuid, verifier_basis=instrument.basis.basis_uuid,
                       evidence="fixture:fresh-independent-capacity-assessment", until=instrument.end)
        restore = dict(organism_uuid=lo.organism_id, loss_uuid=confirmation.request_uuid,
                       credential=genesis.founder.credential, request_uuid=uuid4())
        with self.assertRaisesRegex(r.ContinuityRefused, "regained capacity"):
            r.restore_founder(**restore, finding_uuid=confirmation.finding.determination_uuid)
        for beneficiary in (genesis.founder, genesis.nominee):
            with self.assertRaisesRegex(r.ContinuityRefused, "non-beneficiary"):
                r.confirm_regained_capacity(**confirm, credential=beneficiary.credential)
        with self.assertRaisesRegex(r.ContinuityRefused, "Fresh evidence"):
            r.confirm_regained_capacity(**(confirm | {"evidence": confirmation.finding.evidence_reference}),
                                        credential=confirmer.credential)
        fact = r.confirm_regained_capacity(**confirm, credential=confirmer.credential)
        self.assertEqual(fact.determiner_id, confirmer.pk)
        self.assertIsNone(capacity(genesis.founder, "EDUCATION", fixtures.database_now()),
                          "The factual confirmation alone cannot restore the Founder.")
        with self.assertRaisesRegex(r.ContinuityRefused, "Only the Founder"):
            r.restore_founder(**(restore | {"credential": genesis.nominee.credential}), finding_uuid=fact.determination_uuid)
        with self.assertRaisesRegex(IntegrityError, "R_RESTORATION_AUTHORITY_OR_EVIDENCE"), transaction.atomic():
            models.RootCapacityRestoration.objects.create(
                request_uuid=uuid4(), loss=confirmation, finding=fact, actor=genesis.nominee,
                actor_epoch=genesis.nominee.access_epoch,
                founder_office=models.ContextualRoleAssignment.objects.get(
                    membership__living_organism=lo, role_definition__code="INTEVIA_FOUNDER_STEWARD"))
        # Exercise the real S contest in an isolated branch of this one witness.
        with transaction.atomic():
            determinations.invalidate(alias="default", scope=scope, finding_id=fact.determination_uuid,
                                      mandate_id=instrument.grant.determination_uuid, credential=confirmer.credential,
                                      verifier_basis=instrument.basis.basis_uuid, evidence="fixture:assessment-disputed")
            with self.assertRaisesRegex(r.ContinuityRefused, "undisputed"):
                r.restore_founder(**restore, finding_uuid=fact.determination_uuid)
            self.assertFalse(models.RootCapacityRestoration.objects.filter(loss=confirmation).exists())
            self.assertIsNone(capacity(genesis.founder, "EDUCATION", fixtures.database_now()))
            transaction.set_rollback(True)
        before = fixtures.database_now()
        receipt = r.restore_founder(**restore, finding_uuid=fact.determination_uuid)
        self.assertEqual((receipt.actor_id, receipt.finding_id, receipt.loss_id),
                         (genesis.founder_id, fact.pk, confirmation.pk))
        self.assertEqual(r.restore_founder(**restore, finding_uuid=fact.determination_uuid).pk, receipt.pk)
        self.assertEqual(capacity(genesis.nominee, "EDUCATION", before), "ACTIVATION:" + str(activation.request_uuid))
        self.assertIsNone(capacity(genesis.nominee, "EDUCATION", receipt.recorded_at))
        self.assertEqual(capacity(genesis.founder, "EDUCATION", receipt.recorded_at),
                         "FOUNDER:" + str(receipt.founder_office_id))
        self.assertTrue(models.RootContinuityActivation.objects.filter(pk=activation.pk, envelope=activation.envelope).exists())
        self.guards()

    def test_founder_only_root_values_and_accountability(self):
        """R-07. Both replaceable values preserve predecessors and reject a beneficiary as confirmer."""
        from src.intevia.services import root_continuity as r
        lo = self.successful(support.command())
        genesis = models.RootGenesis.objects.get(organism_uuid=lo.organism_id)
        def change(setting, person, **extra):
            args = dict(credential=genesis.founder.credential, organism_uuid=lo.organism_id,
                        request_uuid=uuid4(), setting=setting, person_uuid=person.identity_id)
            return r.set_value(**(args | extra))
        first, second, new_successor = (support.continuity_person() for _ in range(3))
        at = fixtures.database_now()
        for event in ("DEATH", "INCAPACITY"):
            self.assertEqual(r.query("default", "SELECT public.r_successor(%s,%s,%s,%s)", lo.pk, event, at, at),
                             genesis.nominee_id, "The independently registered confirmer must be compared with the named successor.")
        a = change("CONFIRMER", first)
        b = change("CONFIRMER", second)
        self.assertEqual(b.predecessor_id, a.pk, "The replaced confirmer must remain in the history.")
        self.assertEqual(change("CONFIRMER", second, request_uuid=b.request_uuid).pk, b.pk)
        with self.assertRaisesRegex(r.ContinuityRefused, "different act"):
            change("CONFIRMER", first, request_uuid=b.request_uuid)
        with self.assertRaisesRegex(r.ContinuityRefused, "distinct"):
            change("EMERGENCY_STEWARD", second)
        replacement = change("EMERGENCY_STEWARD", new_successor)
        original_nomination = genesis.nominations.get(definition__code="INTEVIA_EMERGENCY_STEWARD")
        self.assertEqual(original_nomination.nominee_id, genesis.nominee_id)
        self.assertEqual(replacement.person, new_successor)
        at = fixtures.database_now()
        self.assertEqual(r.query("default", "SELECT public.r_successor(%s,'INCAPACITY',%s,%s)", lo.pk, at, at), new_successor.pk)
        self.assertEqual(r.query("default", "SELECT public.r_successor(%s,'DEATH',%s,%s)", lo.pk, at, at), genesis.nominee_id)
        with self.assertRaisesRegex(r.ContinuityRefused, "Founder Steward"):
            change("CONFIRMER", first, credential=genesis.nominee.credential)
        with self.assertRaises(DatabaseError), transaction.atomic():
            models.RootValueDecision.objects.filter(pk=b.pk).update(person=first)
        with self.assertRaisesRegex(IntegrityError, "R_VALUE_AUTHORITY_OR_PREDECESSOR"), transaction.atomic():
            models.RootValueDecision.objects.bulk_create([models.RootValueDecision(
                organism=lo, setting="CONFIRMER", person=first, actor=genesis.nominee,
                actor_epoch=genesis.nominee.access_epoch, founder_office=b.founder_office, predecessor=b,
                effective_at=fixtures.database_now(), request_uuid=uuid4())])
        with self.assertRaisesRegex(IntegrityError, "R_FOUNDER_CENSURE_ONLY"), transaction.atomic(), connection.cursor() as cursor:
            at = fixtures.database_now()
            fixtures.append_event(cursor, "core_authorityinvalidationevent", lo.founding_authority_basis_id,
                                  1, "SUSPEND", None, "SUSPENDED", genesis.founder_id,
                                  lo.founding_authority_basis_id, uuid4().hex, eff=at, occ=at, rec=at,
                                  extra={"kind": "SUSPENSION"})
        self.assertEqual(models.RootValueDecision.objects.count(), 3)
        self.assertEqual(self.fold("core_contextualroleassignment", b.founder_office_id), "ACTIVE")
        self.guards()
