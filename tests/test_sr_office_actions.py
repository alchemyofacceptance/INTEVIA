"""C5: real office commands on board/custom definitions, under installed guards.

Fixture latitude: registration uses the installed disposable-test terms and
founding principal; added membership uses a labelled guarded JOIN, not admission.
No trigger, constraint, function or schema is changed. No archive is imported.
The beta composer supplies access; T04 substitutes only the receipt entitlement
reader to examine the unavailable-access boundary, not a physical expiry event.
TAR, explicit gaps, raw Python types and one-defect breaks accompany the carrier.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from threading import Barrier, Event, Lock
from time import monotonic
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4

from django.db import connection, connections, transaction, DatabaseError
from django.test import TransactionTestCase
from core import models
from core.organism_contract import OrganismAction
from src.intevia.services import organism_role_service as roles
from src.intevia.services import organism_role_definition_service as definitions
from src.intevia.services import organism_office_consent_service as consent
from src.intevia.services import organism_application_service as applications
from src.intevia.services import organism_governance_service as governance
from src.intevia.services.organism_formation_service import OrganismFormationService, FoundLivingOrganismCommand, OrganismPhaseAdvanceService, PhaseAdvanceCommand
from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
from src.intevia.services.organism_authority import S015Authority, S015AuthorityDenied
from src.intevia.services.organism_command_support import S015CommandReplayMismatch, S015CommandReceiptMalformed, S015CommandContended, command_mutex_key
import tests.test_sr_definitions as c3
import tests.s015_0021_support as fixtures


class SROfficeActionTests(TransactionTestCase):
    reset_sequences = True
    _command = c3.SRDefinitionCommandTests._command
    _success = c3.SRDefinitionCommandTests._success
    _define = c3.SRDefinitionCommandTests._define
    _custom_entitlement = c3.SRDefinitionCommandTests._custom_entitlement
    _define_custom = c3.SRDefinitionCommandTests._define_custom

    def setUp(self):
        super().setUp()
        self._start_organism()

    def _start_organism(self):
        fixtures._require_disposable_fixture_database()
        self.tag = uuid4().hex
        self.actor = fixtures.register_test_learner(username="c5-founder-" + self.tag)
        with transaction.atomic(), connection.cursor() as cursor:
            fixtures.seed_fixture_substitute_authority(cursor, "c5-principal-" + self.tag)
        at = fixtures.database_now()
        terms = fixtures.registration_terms_version_in_force(effective_at=at, received_at=at)
        found = OrganismFormationService(clock=fixtures.database_now).found_living_organism(FoundLivingOrganismCommand(
            credential=self.actor.credential, name="C5 " + self.tag, purpose="Test office acts under their recorded rules",
            request_reference="c5-found:" + self.tag, idempotency_key="c5-found:" + self.tag,
            accepted_terms_version_uuid=terms.terms_version_uuid))
        self.organism = models.LivingOrganism.objects.get(organism_id=found.living_organism_uuid)
        at = fixtures.database_now()
        OrganismPhaseAdvanceService(clock=fixtures.database_now).advance_phase(PhaseAdvanceCommand(
            credential=self.actor.credential,living_organism_uuid=self.organism.organism_id,
            from_phase="CONCEIVING",to_phase="GERMINATING",request_reference="c5-phase:"+self.tag,
            idempotency_key="c5-phase:"+self.tag,effective_at=at))
        self.organism.refresh_from_db()
        self.assertEqual(self.organism.state,"ACTIVE","Preparing the office witnesses should reach an active organisation after phase advancement; its state differs.")
        self.founder_member = models.OrganismMembership.objects.get(identity=self.actor, living_organism=self.organism)
        event = models.LivingOrganismEvent.objects.get(living_organism=self.organism, sequence=1)
        self.ids = {"lo": self.organism.pk, "identity": self.actor.pk, "membership": self.founder_member.pk,
                    "basis": self.organism.founding_authority_basis_id, "events": {"core_livingorganismevent": event.pk}}
        self.provider = ProductionApplicationAuthorityProvider()
        self.authority = S015Authority(provider=self.provider)
        self.service = definitions.OrganismRoleDefinitionService(authority=self.authority, clock=fixtures.database_now)
        self.roles = roles.OrganismRoleService(authority=self.authority, clock=fixtures.database_now)
        self.consent = consent.OfficeAppointmentConsentService(authority=self.authority, clock=fixtures.database_now)
        # Use real effect commands: no synthetic configuration record or stale event attribution.
        gateway = applications.OrganismApplicationService(authority=self.authority, clock=fixtures.database_now)
        at = fixtures.database_now(); key = uuid4().hex
        draft = gateway.save_gateway_draft(applications.GatewayDraftCommand(
            credential=self.actor.credential, living_organism_id=self.organism.organism_id,
            action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, request_reference="c5-draft:"+key,
            idempotency_key=key, occurred_at=at, effective_at=at,
            fields={"cq_active":False,"governance_enabled":True,"inherit_portal_cq_settings":False,
                    "require_cq_to_change_lo_settings":False,"require_cq_to_change_application_question":False}))
        at = fixtures.database_now(); key = uuid4().hex
        gateway.effect_gateway_version(applications.EffectGatewayVersionCommand(
            credential=self.actor.credential, living_organism_id=self.organism.organism_id,
            action=OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION, version_uuid=draft.version_uuid,
            request_reference="c5-effect:"+key, idempotency_key=key, occurred_at=at, effective_at=at))

    def _member(self):
        identity = fixtures.register_test_learner(username="c5-member-"+uuid4().hex)
        at = fixtures.database_now(); member_uuid = uuid4()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("""INSERT INTO core_organismmembership
                (membership_uuid,state,created_at,current_state_effective_at,state_known_at,
                 state_source_event_set_fingerprint,identity_id,living_organism_id)
                VALUES (%s,'ACTIVE',now(),%s,%s,%s,%s,%s) RETURNING id""",
                [member_uuid,at,at,fixtures.empty_fp(cursor,"core_organismmembership",member_uuid),identity.pk,self.organism.pk])
            pk = cursor.fetchone()[0]
            fixtures.append_event(cursor,"core_organismmembershiptransition",pk,1,"JOIN",None,"ACTIVE",
                identity.pk,self.ids["basis"],"c5-member:"+uuid4().hex,
                extra={"reason_class":"TEST_ONLY_ADMISSION"},eff=at,occ=at,rec=at)
            fixtures.refresh(cursor,"core_organismmembership",pk,at)
        return models.OrganismMembership.objects.select_related("identity__credential").get(pk=pk)

    def _snapshot(self):
        names = ("LivingOrganism","LivingOrganismEvent","OrganismMembership","OrganismMembershipTransition",
                 "OrganismRoleDefinition","OrganismRoleDefinitionProvision","ContextualRoleAssignment",
                 "ContextualRoleAssignmentStateEvent","OfficeAppointmentConsent","AuthorityDecision",
                 "OrganismCommandReceipt","CommandIntake","Circle","CircleStateEvent","CircleParticipation",
                 "CircleParticipationEnd","CircleJoinRequest","CircleAdmissionDecision","GovernedRule",
                 "OrganismEntitlement","OrganismEntitlementEvent","OrganismEntitlementPolicyText")
        return {name:list(getattr(models,name).objects.order_by("pk").values_list()) for name in names}

    def _proposal_command(self, row, member, actor=None):
        at=fixtures.database_now(); key=uuid4().hex
        return roles.ProposeAssignmentCommand(credential=(actor or self.actor).credential,
            request_reference="c5-propose:"+key,idempotency_key=key,occurred_at=at,effective_at=at,
            membership_uuid=member.membership_uuid,role_definition_uuid=row.role_uuid,
            effective_until_state="NOT_ESTABLISHED",effective_until=None)

    def _transition_command(self, assignment, action, actor=None):
        at=fixtures.database_now(); key=uuid4().hex
        return roles.TransitionAssignmentCommand(credential=(actor or self.actor).credential,
            request_reference="c5-"+action.lower()+":"+key,idempotency_key=key,occurred_at=at,effective_at=at,
            assignment_uuid=assignment.assignment_uuid,action=action,resulting_state="ACTIVE" if action=="ASSIGN" else "ENDED",
            effective_until_state="NONE" if action=="ASSIGN" else None,effective_until=None)

    def _consent_command(self, member, assignment, action="CONSENT"):
        proposal=models.ContextualRoleAssignmentStateEvent.objects.get(assignment=assignment,sequence=1)
        at=fixtures.database_now();key=uuid4().hex
        return consent.RecordOfficeConsentCommand(credential=member.identity.credential,
            request_reference="c5-consent:"+key,idempotency_key=key,occurred_at=at,effective_at=at,
            assignment_uuid=assignment.assignment_uuid,proposal_event_uuid=proposal.event_uuid,action=action)

    def _run(self, call, label):
        try:
            return call()
        except Exception as exc:
            code=getattr(exc,"code",None) or getattr(getattr(exc,"refusal_code",None),"value",None)
            self.fail(f"{label} should complete and commit; it instead raised {type(exc).__name__}"+(f" ({code})." if code else "."))

    def _replay(self, call, result, label):
        from src.intevia.services.organism_command_support import WithheldField

        before=self._snapshot(); again=self._run(call,"Repeating "+label)
        audiences = type(result).replay_field_audiences(result)
        for name in type(result).__dataclass_fields__:
            replay_value = getattr(again, name)
            original_value = getattr(result, name)
            if audiences.get(name) == "SUBJECT":
                self.assertTrue(
                    isinstance(replay_value, WithheldField) or replay_value == original_value,
                    f"Repeating {label} should return the same recorded outcome; the {name} field should be unchanged or withheld for privacy, but it differs.")
            else:
                self.assertEqual(
                    replay_value, original_value,
                    f"Repeating {label} should return the same recorded outcome; the {name} field differs.")
        self.assertEqual(self._snapshot(),before,f"Repeating {label} should create no second effect or record; stored data changed.")

    def _nominate(self, row, member=None):
        member=member or self._member(); command=self._proposal_command(row,member)
        result=self._run(lambda:self.roles.propose_assignment(command),"Nominating this member for the office")
        return member,models.ContextualRoleAssignment.objects.get(assignment_uuid=result.assignment_uuid)

    def _appoint(self, row, member=None):
        member,assignment=self._nominate(row,member)
        self._run(lambda:self.consent.record_office_consent(self._consent_command(member,assignment)),"Recording the nominee's consent")
        self._run(lambda:self.roles.transition_assignment(self._transition_command(assignment,"ASSIGN")),"Appointing the consenting nominee")
        return member,assignment

    def _refusal(self, call, expected, code, label, *, provider=False):
        before=self._snapshot()
        try: call()
        except Exception as exc:
            self.assertIsInstance(exc,expected,f"{label} should return the named refusal at its intended layer; another error occurred.")
            actual=getattr(getattr(exc,"disclosed_refusal_code",None),"value",None) if provider else getattr(exc,"code",None)
            self.assertEqual(actual,code,f"{label} should return {code or 'the office-state refusal'}; a different cause was returned.")
        else: self.fail(f"{label} should refuse; the action was accepted.")
        after=self._snapshot(); allowed={"CommandIntake","AuthorityDecision"} if provider else {"CommandIntake"}
        self.assertEqual({k:v for k,v in after.items() if k not in allowed},
                         {k:v for k,v in before.items() if k not in allowed},
                         f"{label} should preserve all business records and receipts; a record changed.")
        self.assertEqual(len(after["CommandIntake"]),len(before["CommandIntake"])+1,
                         f"{label} should retain exactly its new request record; the count differs.")
        if provider:
            self.assertEqual(len(after["AuthorityDecision"]),len(before["AuthorityDecision"])+1,
                             f"{label} should retain exactly one refusal decision; the count differs.")
            self.assertEqual(models.AuthorityDecision.objects.latest("pk").outcome,"REFUSED",
                             f"{label} should record a refused authority decision; it was not refused.")

    def test_01_board_and_custom_office_routes_commit_and_replay(self):
        """W4 / A4-A8: both kinds, all acts, holder and LO endings, fixed receipt terms."""
        for kind in ("BOARD_OFFICE","CUSTOM"):
            command,row=self._define() if kind=="BOARD_OFFICE" else self._define_custom()
            for ending in ("HOLDER_END","END","REASSIGN"):
                with self.subTest(kind=kind,ending=ending):
                    member=self._member(); proposal=self._proposal_command(row,member)
                    proposed=self._run(lambda:self.roles.propose_assignment(proposal),"Nominating a member")
                    self._replay(lambda:self.roles.propose_assignment(proposal),proposed,"the nomination")
                    assignment=models.ContextualRoleAssignment.objects.get(assignment_uuid=proposed.assignment_uuid)
                    request=self._consent_command(member,assignment)
                    accepted=self._run(lambda:self.consent.record_office_consent(request),"Consenting to the nomination")
                    self._replay(lambda:self.consent.record_office_consent(request),accepted,"the consent")
                    appoint=self._transition_command(assignment,"ASSIGN")
                    appointed=self._run(lambda:self.roles.transition_assignment(appoint),"Appointing this consenting nominee")
                    self._replay(lambda:self.roles.transition_assignment(appoint),appointed,"the appointment")
                    stored=models.OrganismCommandReceipt.objects.get(lineage_reference=appointed.lineage_reference).result_payload
                    self.assertEqual((stored["suspension_tolls_term"],stored["suspension_tolls_term_source"]),
                        ("NO","DEFAULT (no settings store)"),"Appointing to this office should record the existing no-pause default and its source; the receipt differs.")
                    end=self._transition_command(assignment,"END" if ending=="HOLDER_END" else ending,
                                                 member.identity if ending=="HOLDER_END" else None)
                    ended=self._run(lambda:self.roles.transition_assignment(end),"Ending the existing appointment")
                    self._replay(lambda:self.roles.transition_assignment(end),ended,"the ending")
                    assignment.refresh_from_db()
                    self.assertEqual(assignment.state,"ENDED","Ending or reassigning should end this appointment; it remains open.")
                    self.assertEqual(list(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment).order_by("sequence").values_list("action",flat=True)),
                        ["PROPOSE","ASSIGN","END" if ending=="HOLDER_END" else ending],
                        "The appointment history should contain nomination, appointment and the chosen ending in order; it differs.")

    def test_02_current_policy_and_stale_definition_refusals_reach_the_service(self):
        """Current rule selects authority; stale PROPOSE/ASSIGN are class C, after it."""
        command=self._command(); content=replace(command.content,nomination_basis="CONSTITUTION",nomination_policy="A",
            provisions=command.content.provisions+(definitions.DefinitionProvision("NOMINATION_ROUTE","library:test#nomination","v1"),))
        result=self._success(replace(command,content=content));row=models.OrganismRoleDefinition.objects.get(role_uuid=result.role_definition_uuid)
        member=self._member();self_command=self._proposal_command(row,member,member.identity)
        result=self._run(lambda:self.roles.propose_assignment(self_command),"Putting yourself forward under constitutional policy A")
        facts=json.loads(result.authority_reference.removeprefix("s015a1:"))
        self.assertEqual((facts["k"],facts["t"],facts["i"]),("A","SELF_PROPOSAL",None),"A free self-nomination should retain policy A without an entitlement; the authority facts differ.")
        _,pending=self._nominate(row)
        nominee=pending.membership
        self._run(lambda:self.consent.record_office_consent(self._consent_command(nominee,pending)),"Consenting before the office definition changes")
        consent_nominee,consent_pending=self._nominate(row)
        changed=self._success(self._command("AMEND",row=row,content=content))
        self._refusal(lambda:self.consent.record_office_consent(self._consent_command(consent_nominee,consent_pending)),
            consent.S015OfficeConsentSuperseded,"SR_SUPERSEDED_DEFINITION","Consenting to the old office definition")
        self._refusal(lambda:self.roles.propose_assignment(self._proposal_command(row,member)),roles.S015AssignmentRuleRefused,
            "SR_SUPERSEDED_DEFINITION","Nominating under the old definition")
        self._refusal(lambda:self.roles.transition_assignment(self._transition_command(pending,"ASSIGN")),roles.S015AssignmentRuleRefused,
            "SR_SUPERSEDED_DEFINITION","Appointing under the old definition")
        row.refresh_from_db(); current=models.OrganismRoleDefinition.objects.get(role_uuid=changed.role_definition_uuid)
        self._nominate(current)
        pending.refresh_from_db()
        self.assertEqual(pending.role_definition_id,row.pk,"Refusing the stale appointment should leave its original definition attached; it was retargeted.")

    def test_03_held_office_is_refused_but_other_offices_confer_no_authority(self):
        """W11/W19b/c: same family refused; another office allowed, no Coordinator or seat."""
        at=fixtures.database_now()
        baseline_count=self.provider._eligible_coordinator_count(self.organism,SimpleNamespace(effective_at=at,evaluated_at=at))
        command,row=self._define(); member,held=self._appoint(row)
        amended=self._success(self._command("AMEND",row=row,content=command.content))
        current=models.OrganismRoleDefinition.objects.get(role_uuid=amended.role_definition_uuid)
        _,duplicate=self._nominate(current,member)
        self.consent.record_office_consent(self._consent_command(member,duplicate))
        self._refusal(lambda:self.roles.transition_assignment(self._transition_command(duplicate,"ASSIGN")),roles.S015AssignmentRuleRefused,
            "SR_OFFICE_ALREADY_HELD","Appointing the same member to the same office again")
        _,other=self._define();before=self._snapshot();_,second=self._appoint(other,member)
        after=self._snapshot()
        for name in ("CircleParticipation","CircleAdmissionDecision","CircleJoinRequest","CircleStateEvent"):
            self.assertEqual(after[name],before[name],"Holding a second office should create no GOVERNANCE seat, admission or Circle event; those records changed.")
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(membership=member,role_definition__code="COORDINATOR",state="ACTIVE").exists(),
                         "Holding board offices should not appoint the member as Coordinator; a Coordinator appointment appeared.")
        at=fixtures.database_now()
        head_refs=tuple(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment__membership=member).values_list("lineage_reference",flat=True))
        observation=SimpleNamespace(effective_at=at,evaluated_at=at,contextual_role_head_references=head_refs)
        self.assertIsNone(self.provider._effective_coordinator_office(observation,member),
                          "The member's two board offices should provide no Coordinator authority; the provider found an effective Coordinator office.")
        self.assertEqual(self.provider._eligible_coordinator_count(self.organism,observation),baseline_count,
                         "Appointing to board offices should leave the eligible Coordinator count unchanged; the provider's count changed.")
        unauthorised=replace(self._command(),credential=member.identity.credential)
        self._refusal(lambda:self.service.record_role_definition(unauthorised),S015AuthorityDenied,"E_NOT_EFFECTIVE_COORDINATOR",
                      "Using a board title to define another office",provider=True)
        for index,assignment in enumerate((held,second)):
            original=assignment.role_definition_id
            self._run(lambda:self.roles.transition_assignment(self._transition_command(assignment,"END",member.identity if index==0 else None)),"Ending the board appointment through the holder or organisation route")
            assignment.refresh_from_db()
            self.assertEqual((assignment.state,assignment.role_definition_id),("ENDED",original),"Resigning should end the appointment while keeping its definition; those facts differ.")
            self.assertFalse(roles.assignment_in_effect(assignment,at=fixtures.database_now()),"An ended appointment should no longer be in effect; it was reported effective.")
            events=list(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment).order_by("sequence"))
            for event in events:
                for sql in ("UPDATE core_contextualroleassignmentstateevent SET evidence_reference = evidence_reference WHERE id = %s",
                            "DELETE FROM core_contextualroleassignmentstateevent WHERE id = %s"):
                    with self.assertRaises(DatabaseError,msg="Office history should refuse even a direct edit or deletion; a stored event was changed."):
                        with transaction.atomic(),connection.cursor() as cursor: cursor.execute(sql,[event.pk])
            self.assertEqual(list(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment).values_list("pk",flat=True)),
                             [e.pk for e in events],"Refusing history edits should retain every office event; event identities changed.")
        at=fixtures.database_now()
        self.assertIsNone(self.provider._effective_coordinator_office(SimpleNamespace(effective_at=at,evaluated_at=at,contextual_role_head_references=head_refs),member),
                          "The ended board offices should still confer no Coordinator authority; the provider found one.")
        self.assertTrue(models.OrganismRoleDefinition.objects.get(pk=other.pk).active,
                        "Ending the board-office appointment should leave its definition available; the definition was retired.")
        # Ending one version opens a fresh nomination; no version is rewritten.
        self._nominate(current,member)

    def test_04_unavailable_custom_access_preserves_existing_chains(self):
        """W10 boundary only: receipt access absent; real expiration lineage is disclosed."""
        command,row=self._define_custom(); member,active=self._appoint(row)
        _,pending=self._nominate(row)
        self.consent.record_office_consent(self._consent_command(pending.membership,pending))
        _,retirable=self._define_custom()
        before=models.ContextualRoleAssignment.objects.get(pk=active.pk).state
        with patch.object(definitions,"custom_role_entitled",return_value=False):
            self._refusal(lambda:self.roles.propose_assignment(self._proposal_command(row,self.founder_member)),S015AuthorityDenied,
                          "E_CUSTOM_ROLE_NOT_ENTITLED","Starting a new custom-role nomination without access",provider=True)
            self.assertEqual(models.ContextualRoleAssignment.objects.get(pk=active.pk).state,before,
                             "Losing access should not remove an existing custom-role holder; the appointment changed.")
            self._run(lambda:self.roles.transition_assignment(self._transition_command(pending,"ASSIGN")),"Appointing on the previously opened custom-role chain")
            self._run(lambda:self.roles.transition_assignment(self._transition_command(active,"END",member.identity)),"Resigning after custom-role access is unavailable")
            self._run(lambda:self.roles.transition_assignment(self._transition_command(pending,"REASSIGN")),"Ending for reassignment after access is unavailable")
            self._success(self._command("RETIRE",row=retirable))

    def test_05_named_body_uses_only_its_recorded_alternative(self):
        """A6 missing route class A; later alternative cannot authorise the old chain."""
        command=self._command();content=replace(command.content,appointing_authority="NAMED_BODY",appointing_body_reference="library:test#body",
            provisions=command.content.provisions+(definitions.DefinitionProvision("APPOINTING_AUTHORITY","library:test#body","v1"),))
        result=self._success(replace(command,content=content));row=models.OrganismRoleDefinition.objects.get(role_uuid=result.role_definition_uuid)
        member,old=self._nominate(row);self.consent.record_office_consent(self._consent_command(member,old))
        self._refusal(lambda:self.roles.transition_assignment(self._transition_command(old,"ASSIGN")),S015AuthorityDenied,
            "C_APPOINTING_ROUTE_NOT_IMPLEMENTED","Appointing through a named body without its route",provider=True)
        updated=replace(content,alternative_route_reference="TEST explicit Coordinator alternative",
            provisions=content.provisions+(definitions.DefinitionProvision("ALTERNATIVE_ROUTE","library:test#alternative","v1"),))
        new=self._success(self._command("AMEND",row=row,content=updated));current=models.OrganismRoleDefinition.objects.get(role_uuid=new.role_definition_uuid)
        self._refusal(lambda:self.roles.transition_assignment(self._transition_command(old,"ASSIGN")),S015AuthorityDenied,
            "C_APPOINTING_ROUTE_NOT_IMPLEMENTED","Borrowing a later alternative for an earlier nomination",provider=True)
        member,new=self._nominate(current);self.consent.record_office_consent(self._consent_command(member,new))
        result=self._run(lambda:self.roles.transition_assignment(self._transition_command(new,"ASSIGN")),"Appointing through the expressly recorded alternative")
        facts=json.loads(result.authority_reference.removeprefix("s015a1:"))
        self.assertEqual((facts["g"],facts["y"]),(str(current.role_uuid),"AR"),"An alternative appointment should name its own version and alternative route; the facts differ.")

    def test_06_two_definition_contenders_cannot_both_create_one_office(self):
        """W12: two distinct requests by the Coordinator meet before locking; one wins."""
        first=self._command();second=replace(first,request_reference="c5-race:"+uuid4().hex,idempotency_key=uuid4().hex)
        barrier=Barrier(2,timeout=15);original=definitions._lock_actor
        first_locked=Event();release=Event();mutex=Lock();pids=[]
        def meet_then_lock(*args,**kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pid=cursor.fetchone()[0]
            with mutex: pids.append(pid)
            barrier.wait()
            actor=original(*args,**kwargs)
            with mutex:
                first=not first_locked.is_set()
                if first: first_locked.set()
            if first and not release.wait(15):
                raise RuntimeError("The race observer did not release the first contender in time")
            return actor
        def contender(command):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'");cursor.execute("SET statement_timeout = '20s'")
                result=definitions.OrganismRoleDefinitionService(authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),clock=fixtures.database_now).record_role_definition(command)
                return ("success",str(result.role_definition_uuid))
            except definitions.S015RoleDefinitionRefused as exc: return ("refused",exc.code)
            except Exception as exc: return ("error",type(exc).__name__,str(exc).splitlines()[0] if str(exc) else "")
            finally: connections.close_all()
        with patch.object(definitions,"_lock_actor",meet_then_lock):
            with ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(contender,first);b=pool.submit(contender,second)
                observed=False
                try:
                    self.assertTrue(first_locked.wait(10),"The two definition contenders should reach the actor lock; neither reported acquiring it.")
                    deadline=monotonic()+5
                    while monotonic()<deadline:
                        with mutex: observed_pids = list(pids)
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pid, pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid = ANY(%s)",[observed_pids])
                            waits=cursor.fetchall()
                        if any(set(blockers)&set(observed_pids) for pid,blockers in waits):
                            observed=True;break
                        release.wait(0.02)
                finally: release.set()
                outcomes=[a.result(timeout=30),b.result(timeout=30)]
        self.assertTrue(observed,"The competing office definitions should serialize on a real database lock; no contender was observed waiting for the other.")
        self.assertEqual(sorted(x[0] for x in outcomes),["refused","success"],f"Two attempts to define the same office should yield one committed definition and one refusal; outcomes were {outcomes}.")
        self.assertEqual(next(x[1] for x in outcomes if x[0]=="refused"),"SR_DEFINITION_EXISTS","The losing definition attempt should explain that the office already exists; another refusal occurred.")
        self.assertEqual(models.OrganismRoleDefinition.objects.filter(living_organism=self.organism,code=first.content.code).count(),1,
                         "The two contenders should leave exactly one definition of this office; the count differs.")

    def _contended_then_retry(self, execute, command, action, label):
        actor=models.Identity.objects.get(credential=command.credential)
        key=command_mutex_key(actor_identity_id=actor.identity_id,action=action,idempotency_key=command.idempotency_key)
        acquired=Event();release=Event()
        def hold():
            connections.close_all()
            try:
                with transaction.atomic(),connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)",[key])
                    acquired.set()
                    if not release.wait(15): raise RuntimeError("The contention witness did not release its fixture lock")
            finally: connections.close_all()
        before=self._snapshot()
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout");old_timeout=cursor.fetchone()[0]
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker=pool.submit(hold)
            try:
                self.assertTrue(acquired.wait(5),f"Preparing contention for {label} should acquire the intended command lock; it did not.")
                with connection.cursor() as cursor: cursor.execute("SELECT set_config('lock_timeout','250ms',false)")
                try: execute(command)
                except Exception as exc:
                    self.assertIsInstance(exc,S015CommandContended,f"A lock conflict during {label} should become the known contention explanation after rollback; another error occurred.")
                    self.assertEqual(exc.diagnostic_sqlstate,"55P03",f"The controlled lock conflict during {label} should report lock unavailability; the database cause differs.")
                    self.assertIsNotNone(exc.received_at,f"The conflict during {label} should retain its intake time; that time is missing.")
                else: self.fail(f"The held command lock should stop {label}; it was allowed to proceed.")
            finally:
                release.set()
                with connection.cursor() as cursor: cursor.execute("SELECT set_config('lock_timeout',%s,false)",[old_timeout])
                worker.result(timeout=20)
        after=self._snapshot()
        self.assertEqual({k:v for k,v in after.items() if k!="CommandIntake"},{k:v for k,v in before.items() if k!="CommandIntake"},
                         f"A lock conflict during {label} should preserve all governed records; records changed.")
        self.assertEqual(len(after["CommandIntake"]),len(before["CommandIntake"])+1,
                         f"A lock conflict during {label} should retain exactly one new request; the count differs.")
        result=self._run(lambda:execute(command),"Retrying "+label+" after the lock is released")
        self.assertEqual(models.CommandIntake.objects.count(),len(after["CommandIntake"]),
                         f"Retrying {label} unchanged should reuse the original request; a second intake record appeared.")
        return result

    def test_07_all_office_entries_translate_real_contention_and_retry(self):
        """A4-A8 class D: real advisory-lock conflicts roll back, then exact retries work."""
        _,row=self._define();member=self._member()
        p=self._proposal_command(row,member)
        proposed=self._contended_then_retry(self.roles.propose_assignment,p,OrganismAction.TRANSITION_CONTEXTUAL_ROLE,"the nomination")
        a=models.ContextualRoleAssignment.objects.get(assignment_uuid=proposed.assignment_uuid)
        c=self._consent_command(member,a)
        self._contended_then_retry(self.consent.record_office_consent,c,OrganismAction.RECORD_OFFICE_CONSENT,"the consent")
        assign=self._transition_command(a,"ASSIGN")
        self._contended_then_retry(self.roles.transition_assignment,assign,OrganismAction.TRANSITION_CONTEXTUAL_ROLE,"the appointment")
        end=self._transition_command(a,"END")
        self._contended_then_retry(self.roles.transition_assignment,end,OrganismAction.TRANSITION_CONTEXTUAL_ROLE,"the ending")
        _,a2=self._appoint(row)
        reassign=self._transition_command(a2,"REASSIGN")
        self._contended_then_retry(self.roles.transition_assignment,reassign,OrganismAction.TRANSITION_CONTEXTUAL_ROLE,"the reassignment")

    def _seat_founder(self):
        service=governance.OrganismGovernanceService(clock=fixtures.database_now,authority=self.authority)
        at=fixtures.database_now();rule_uuid=uuid4();key=uuid4().hex
        body={"seats":[{"seat_id":"founder","eligibility":"LO_MEMBER","admission_route":"APPOINTMENT",
              "deciding_body":"office:LO_COORDINATOR","term":None,"vacancy_rule":"BY_SEAT_ROUTE","effective_from":at.isoformat()}],
              "officer_roles":[],"election_schedule":None}
        service.adopt_governing_instrument(governance.AdoptGoverningInstrumentCommand(
            credential=self.actor.credential,living_organism_uuid=self.organism.organism_id,rule_uuid=rule_uuid,
            version=1,body=body,effective_from=at,effective_at=at,request_reference="c5-arrangement:"+key,idempotency_key=key))
        key=uuid4().hex
        created=service.create_governance_circle(governance.CreateGovernanceCircleCommand(
            credential=self.actor.credential,living_organism_uuid=self.organism.organism_id,
            request_reference="c5-circle:"+key,idempotency_key=key,effective_at=fixtures.database_now()))
        key=uuid4().hex
        seated=service.seat_governance_circle(governance.SeatFoundingGovernanceCircleCommand(
            credential=self.actor.credential,living_organism_uuid=self.organism.organism_id,circle_uuid=created.record_uuid,
            seat_rule_uuid=rule_uuid,seat_id="founder",request_reference="c5-seat:"+key,idempotency_key=key,
            effective_at=fixtures.database_now()))
        return seated,created

    def test_08_participation_is_required_and_its_creation_cut_is_observed(self):
        """TAR06: live GOVERNANCE participation permits both nomination and appointment.

        The historical observation before creation is a query boundary, not a
        claim that a participation may lawfully predate its Circle. The closed-Circle and ended-participation branches retain their lawful
        surrounding state; redundant query clauses are not claimed independently.
        """
        command=self._command(); content=replace(command.content,participation_required=True,
            provisions=command.content.provisions+(definitions.DefinitionProvision("PARTICIPATION_REQUIRED","library:test#participation","v1"),))
        defined=self._success(replace(command,content=content));row=models.OrganismRoleDefinition.objects.get(role_uuid=defined.role_definition_uuid)
        self._refusal(lambda:self.roles.propose_assignment(self._proposal_command(row,self.founder_member)),S015AuthorityDenied,
            "E_NOMINEE_NOT_ELIGIBLE","Nominating without the required GOVERNANCE participation",provider=True)
        seated,created=self._seat_founder()
        participation=models.CircleParticipation.objects.get(participation_uuid=seated.participation_uuid)
        circle=models.Circle.objects.get(circle_uuid=created.record_uuid)
        creation=models.CircleStateEvent.objects.get(circle=circle,sequence=1)
        self.assertEqual(creation.action,"CREATE_DORMANT","The participating GOVERNANCE Circle should have its stored creation event; its first action differs.")
        proposed=self._run(lambda:self.roles.propose_assignment(self._proposal_command(row,self.founder_member,self.actor)),
                           "Nominating the current GOVERNANCE participant")
        facts=json.loads(proposed.authority_reference.removeprefix("s015a1:"))
        self.assertEqual(facts["e"],str(participation.participation_uuid),"The nomination should cite this actual participation, not a placeholder; its evidence differs.")
        assignment=models.ContextualRoleAssignment.objects.get(assignment_uuid=proposed.assignment_uuid)
        # Self-proposal carries consent. It must reach ASSIGN without a fabricated consent.
        self._run(lambda:self.roles.transition_assignment(self._transition_command(assignment,"ASSIGN")),"Appointing the participating self-nominee")
        cut=fixtures.database_now()
        request=SimpleNamespace(effective_at=cut,evaluated_at=cut)
        self.assertEqual(self.provider._office_participation_reference(request,self.organism,self.founder_member),str(participation.participation_uuid),
                         "Observing the recorded participant after Circle creation should return that participation; the reference differs.")
        before_creation=SimpleNamespace(effective_at=cut,evaluated_at=creation.recorded_at-timedelta(microseconds=1))
        self.assertIsNone(self.provider._office_participation_reference(before_creation,self.organism,self.founder_member),
                          "Observing before the Circle was known should return no participation; a later fact leaked into the observation.")

        pending_result=self.roles.propose_assignment(self._proposal_command(row,self.founder_member,self.actor))
        pending=models.ContextualRoleAssignment.objects.get(assignment_uuid=pending_result.assignment_uuid)
        # Guarded domain fixture: close the Circle through its append-only chain.
        # This is not a claim to test a user-facing Circle-closing command.
        at=fixtures.database_now()
        head=models.CircleStateEvent.objects.filter(circle=circle).latest("sequence")
        with transaction.atomic(),connection.cursor() as cursor:
            fixtures.append_event(cursor,"core_circlestateevent",circle.pk,head.sequence+1,"CLOSE",head.resulting_state,"CLOSED",
                self.actor.pk,self.ids["basis"],"c5-circle-close:"+uuid4().hex,pred_id=head.pk,eff=at,occ=at,rec=at)
            fixtures.refresh(cursor,"core_circle",circle.pk,at)
        self._refusal(lambda:self.roles.propose_assignment(self._proposal_command(row,self.founder_member)),S015AuthorityDenied,
            "E_NOMINEE_NOT_ELIGIBLE","Nominating a member whose GOVERNANCE Circle is closed",provider=True)
        self._refusal(lambda:self.roles.transition_assignment(self._transition_command(pending,"ASSIGN")),S015AuthorityDenied,
            "E_NOMINEE_NOT_ELIGIBLE","Appointing a member whose GOVERNANCE Circle is closed",provider=True)
        # A separate organisation leaves the Circle open while membership and
        # participation end together under U-3; no unsupported independent end.
        self._start_organism()
        seated,created=self._seat_founder()
        participation=models.CircleParticipation.objects.get(participation_uuid=seated.participation_uuid)
        self._member()  # departure is not the last membership
        coordinator=models.ContextualRoleAssignment.objects.get(membership=self.founder_member,role_definition__code="COORDINATOR")
        self.roles.transition_assignment(self._transition_command(coordinator,"END",self.actor))
        from src.intevia.services.organism_membership_service import OrganismMembershipService, TransitionMembershipCommand
        at=fixtures.database_now();key=uuid4().hex
        OrganismMembershipService(authority=self.authority,clock=fixtures.database_now).transition_membership(TransitionMembershipCommand(
            credential=self.actor.credential,request_reference="c5-leave:"+key,idempotency_key=key,
            occurred_at=at,effective_at=at,membership_uuid=self.founder_member.membership_uuid,
            resulting_state="ENDED",reason_class="LEAVE"))
        self.assertTrue(models.CircleParticipationEnd.objects.filter(participation=participation).exists(),
                        "Leaving the organisation should end the member's GOVERNANCE participation; no ending was recorded.")
        at=fixtures.database_now()
        self.assertIsNone(self.provider._office_participation_reference(SimpleNamespace(effective_at=at,evaluated_at=at),self.organism,self.founder_member),
                          "Observing an ended participation should return no eligibility evidence; the ended record was returned.")

    def test_09_end_and_reassign_keep_old_versions_and_terms_do_not_pause(self):
        """W19c/P4: both kinds retain old versions; every ending route remains available."""
        for kind in ("BOARD_OFFICE","CUSTOM"):
            command,row=self._define() if kind=="BOARD_OFFICE" else self._define_custom()
            appointments=[self._appoint(row) for _ in range(3)]
            amended=self._success(self._command("AMEND",row=row,content=command.content))
            for (member,assignment),action,by_holder in zip(appointments,("END","END","REASSIGN"),(True,False,False)):
                invalid=replace(self._transition_command(assignment,action,member.identity if by_holder else None),resulting_state="ACTIVE")
                self._refusal(lambda:self.roles.transition_assignment(invalid),roles.S015AssignmentEdgeRefused,None,
                              "Ending an office while asking to leave it active")
                result=self._run(lambda:self.roles.transition_assignment(self._transition_command(assignment,action,member.identity if by_holder else None)),
                                 "Ending an appointment under its original office definition")
                facts=json.loads(result.authority_reference.removeprefix("s015a1:"))
                self.assertEqual((facts["g"],facts["k"]),(str(row.role_uuid),action),"Ending the old appointment should cite its original definition and chosen action; the facts differ.")
                self.assertNotIn("y",facts,"Ending an existing appointment should not claim a new alternative appointing route; one appeared in its evidence.")
                assignment.refresh_from_db()
                self.assertEqual((assignment.state,assignment.role_definition_id),("ENDED",row.pk),"The ended appointment should retain its original definition; the stored link or state differs.")
            current=models.OrganismRoleDefinition.objects.get(role_uuid=amended.role_definition_uuid)
            member,assignment=self._nominate(current)
            self.consent.record_office_consent(self._consent_command(member,assignment))
            command=self._transition_command(assignment,"ASSIGN");until=command.effective_at+timedelta(days=1)
            result=self._run(lambda:self.roles.transition_assignment(replace(command,effective_until_state="FIXED",effective_until=until)),"Recording a fixed-term appointment")
            assignment.refresh_from_db();before=self._snapshot()
            self.assertTrue(roles.assignment_in_effect(assignment,until-timedelta(microseconds=1)),"A fixed-term appointment should be in effect immediately before its end; it was already ineffective.")
            self.assertFalse(roles.assignment_in_effect(assignment,until),"A fixed-term appointment should cease to be effective at its exact end without pausing; it remained effective.")
            self.assertEqual(self._snapshot(),before,"Observing term expiry should neither end nor rewrite the appointment; a record changed.")
            self.assertEqual(models.OrganismCommandReceipt.objects.get(lineage_reference=result.lineage_reference).result_payload["suspension_tolls_term"],"NO","The fixed-term receipt should retain the no-pause default; its value differs.")

    def test_10_all_office_entries_reject_changed_retries_and_bad_receipts(self):
        """A4-A8 technical replay matrix, using the real first write for every entry.

        Malformed receipt input is substituted at the decoder boundary; immutable
        stored receipts are never edited to manufacture the technical condition.
        """
        _,row=self._define();member=self._member()
        p=self._proposal_command(row,member);proposed=self.roles.propose_assignment(p)
        a=models.ContextualRoleAssignment.objects.get(assignment_uuid=proposed.assignment_uuid)
        c=self._consent_command(member,a);self.consent.record_office_consent(c)
        assign=self._transition_command(a,"ASSIGN");self.roles.transition_assignment(assign)
        end=self._transition_command(a,"END");self.roles.transition_assignment(end)
        m2,a2=self._appoint(row);reassign=self._transition_command(a2,"REASSIGN");self.roles.transition_assignment(reassign)
        actions=[(self.roles,self.roles.propose_assignment,p,"the nomination"),
                 (self.consent,self.consent.record_office_consent,c,"the consent"),
                 (self.roles,self.roles.transition_assignment,assign,"the appointment"),
                 (self.roles,self.roles.transition_assignment,end,"the ending"),
                 (self.roles,self.roles.transition_assignment,reassign,"the reassignment")]
        for service,execute,command,label in actions:
            with self.subTest(action=label):
                before=self._snapshot()
                try: execute(replace(command,occurred_at=command.occurred_at-timedelta(microseconds=1)))
                except Exception as exc:
                    self.assertIsInstance(exc,S015CommandReplayMismatch,f"Repeating {label} with changed timing should return the changed-request refusal; another error occurred.")
                else: self.fail(f"Repeating {label} with changed timing should refuse; changed content was accepted.")
                self.assertEqual(self._snapshot(),before,f"A changed retry of {label} should preserve all recorded data; data changed.")
                with patch.object(service,"_replay_result",side_effect=S015CommandReceiptMalformed()):
                    try: execute(command)
                    except Exception as exc:
                        self.assertIsInstance(exc,S015CommandReceiptMalformed,f"Reading an invalid receipt for {label} should preserve the decoder's technical refusal; another error occurred.")
                    else: self.fail(f"Reading an invalid receipt for {label} should stop with a technical error; a result was served.")
                self.assertEqual(self._snapshot(),before,f"A malformed replay of {label} should not create or change any record; data changed.")
