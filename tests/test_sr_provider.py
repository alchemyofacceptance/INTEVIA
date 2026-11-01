"""SR C4/C5: six focused witnesses; TAR and fixture limits are in the return ledger.

No archive imports. C3's disposable F0 fixture is retained and labelled: its
seeded identity has incomplete historical registration evidence. The challenged
provider is production; nomination/appointment setup uses C2 guarded writers and
its test-only decision provider, never C2's definition guard suspension. C3's
real command writes definitions with their guards active. No schema mutation.
Raw reads used by imported helpers: UUID -> UUID, id/count -> int, bool -> bool,
timestamptz -> aware datetime, to_jsonb(row)::text -> str. These tests use ORM for
snapshots and read no raw JSON. No worker, sleep, retry or shared mutable fixture.
"""
from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
from uuid import uuid4

from django.db import connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from core import models
from core.organism_contract import OrganismAction
from src.intevia.services import organism_application_authority as provider_module
from src.intevia.services import organism_role_definition_service as definitions
from src.intevia.services import organism_office_consent_service as consent_service
from src.intevia.services.organism_authority import (
    S015Authority, S015AuthorityDenied, S015PostFoundingAuthorityResponse,
    S015PostFoundingAuthorityRefusal, authority_target_fingerprint,
)
from src.intevia.services.organism_command_support import _authority_request
from src.intevia.services.organism_role_service import (
    OrganismRoleService, TransitionAssignmentCommand, S015OperationNotYetImplemented,
)
import tests.test_sr_definitions as c3
from tests.test_sr_office_guards import _add_member, _propose, _consent
from tests.s015_0021_support import database_now, found_nonroot, seed_test_gateway


class SRProviderPureTests(SimpleTestCase):
    def test_01_vacancy_observation_uses_one_cut_and_support_explains_missing_context(self):
        """O-09: one vacancy observation uses one clock cut and reports a missing organisation."""
        at = datetime.now(timezone.utc)
        organism = SimpleNamespace(pk=1)
        rows = [SimpleNamespace(role_uuid=uuid4(), code=code, provisions=SimpleNamespace(all=lambda: []))
                for code in ("TEST_BOARD_A", "TEST_BOARD_B")]
        lo = MagicMock()
        lo.using.return_value.get.return_value = organism
        roles = MagicMock()
        roles.using.return_value.filter.return_value.prefetch_related.return_value.order_by.return_value = rows
        cursor = MagicMock()
        # Return the database type for each real query; extra clock reads remain
        # observable as assertion failures rather than exhausting a mock list.
        cursor.fetchone.side_effect = lambda: ((at,) if cursor.execute.call_args.args[0] == "SELECT clock_timestamp()" else (False,))
        database = MagicMock()
        database.cursor.return_value.__enter__.return_value = cursor
        with patch.object(definitions.LivingOrganism,"objects",lo), patch.object(definitions.OrganismRoleDefinition,"objects",roles), patch.object(definitions,"connections",{"default":database}):
            result = definitions.board_office_vacancies(uuid4())
            self.assertEqual([r.code for r in result],[r.code for r in rows], msg='Reading vacancies for the two board offices should return both offices in their recorded order; the returned list differs.')
            self.assertEqual(cursor.execute.call_args_list.count(call("SELECT clock_timestamp()")), 1, msg='Reading the two board-office vacancies should ask the database for the current time once; the clock requests differ.')
            self.assertEqual([c.args[1] for c in cursor.execute.call_args_list if c.args[0] != "SELECT clock_timestamp()"], [[organism.pk, row.code, at, at] for row in rows], 'Reading both board-office vacancies should query their actual assignment folds at the shared observation time; an office or time cut differs.')
            self.assertFalse(any(r.filled for r in result), msg='Reading vacancies when neither board office has a holder should show both as vacant; an office was shown as filled.')
            lo.using.return_value.get.side_effect = models.LivingOrganism.DoesNotExist
            with self.assertRaises(definitions.S015RoleDefinitionNotFound, msg='Reading vacancies for an organisation that does not exist should refuse with an organisation-not-found explanation; no such refusal occurred.') as missing:
                definitions.board_office_vacancies(uuid4())
            self.assertEqual(missing.exception.support_class,"A", msg='Refusing a vacancy lookup for a missing organisation should be an actionable class-A explanation; a different support class was returned.')
            self.assertEqual(str(missing.exception),definitions.ORGANISM_NOT_FOUND_MESSAGE, msg='Refusing a vacancy lookup for a missing organisation should tell the person to open the intended organisation; the explanation differs.')
        self.assertNotEqual(definitions.ORGANISM_NOT_FOUND_MESSAGE,definitions.DEFINITION_NOT_FOUND_MESSAGE, msg='Explaining a missing organisation and a missing office version should give different guidance; the two explanations are identical.')



class SRProviderTests(TransactionTestCase):
    reset_sequences = True
    # Reuse helpers, not the TestCase inheritance or its test methods.
    _command = c3.SRDefinitionCommandTests._command
    _snapshot = c3.SRDefinitionCommandTests._snapshot
    _success = c3.SRDefinitionCommandTests._success
    _failure = c3.SRDefinitionCommandTests._failure
    _define = c3.SRDefinitionCommandTests._define
    _custom_entitlement = c3.SRDefinitionCommandTests._custom_entitlement
    _define_custom = c3.SRDefinitionCommandTests._define_custom
    _fixture_holder = c3.SRDefinitionCommandTests._fixture_holder

    def setUp(self):
        super().setUp()
        self.assertEqual(connection.vendor,"postgresql","C4 requires the PostgreSQL route")
        self.tag = uuid4().hex
        with transaction.atomic(),connection.cursor() as cur:
            self.ids = found_nonroot(cur,tag="c4-"+self.tag)
            seed_test_gateway(cur,self.ids,include_governance_configuration=True)
        self.organism = models.LivingOrganism.objects.get(pk=self.ids["lo"])
        self.actor = models.Identity.objects.select_related("credential").get(pk=self.ids["identity"])
        self.provider = provider_module.ProductionApplicationAuthorityProvider()
        self.authority = S015Authority(provider=self.provider)
        self.service = definitions.OrganismRoleDefinitionService(authority=self.authority,clock=database_now)

    def _member(self):
        with transaction.atomic(),connection.cursor() as cur:
            pk,identity_pk,member_uuid = _add_member(cur,self.ids,"c4-member:"+uuid4().hex,database_now())
        return models.OrganismMembership.objects.select_related("identity__credential","head_transition").get(pk=pk)

    def _request(self, target, *, actor_member=None, action=OrganismAction.TRANSITION_CONTEXTUAL_ROLE):
        member = actor_member or models.OrganismMembership.objects.select_related("identity__credential","head_transition").get(pk=self.ids["membership"])
        offices = models.ContextualRoleAssignment.objects.filter(membership=member,state="ACTIVE").select_related("head_state_event")
        at = database_now()
        target = {"action":action.value,"living_organism_id":str(self.organism.organism_id),**target}
        return _authority_request(database_alias="default",actor=member.identity,actor_membership=member,
            actor_role_head_refs=tuple(o.head_state_event.lineage_reference for o in offices if o.head_state_event_id),
            action=action,request_reference="c4-provider:"+uuid4().hex,idempotency_key=uuid4().hex,
            received_at=at,effective_at=at,target=target,target_fingerprint=authority_target_fingerprint(target),
            living_organism_id=self.organism.organism_id,
            assignment_uuid=None if "assignment_uuid" not in target else __import__('uuid').UUID(target["assignment_uuid"]))

    def _proposal(self,row,member,*,actor_member=None):
        target = {"event_action":"PROPOSE","membership_uuid":str(member.membership_uuid),"role_definition_uuid":str(row.role_uuid)}
        if row.code != "COORDINATOR":
            target["nomination_rule_source"] = "DEFINITION"
        return self._request(target,actor_member=actor_member)

    def _evaluate(self, request, expected=None, *, action_description):
        before = self._snapshot()
        result = self.provider.evaluate_organism_authority(request)
        actual = c3._outcome_label(result)
        expected_outcome = expected if expected is not None else "qualified"
        sentence = f"{action_description}: expected {expected_outcome}; actual {actual}."
        if self._snapshot() != before:
            self.fail(sentence.rstrip(".") + "; expected no record changes, actual stored records changed.")
        valid = (isinstance(result, S015PostFoundingAuthorityResponse) if expected is None else
                 isinstance(result, S015PostFoundingAuthorityRefusal) and result.refusal_code.value == expected)
        if not valid:
            self.fail(sentence)
        return json.loads(result.authority_reference.removeprefix("s015a1:"))

    def _nomination(self,row,*,consented=False):
        member = self._member()
        tag = "c4-nomination:"+uuid4().hex
        with transaction.atomic(),connection.cursor() as cur:
            aid,auuid,pid = _propose(cur,self.ids,proposer_id=self.actor.pk,proposer_membership_id=self.ids["membership"],
                nominee_membership_id=member.pk,nominee_membership_uuid=member.membership_uuid,
                definition_id=row.pk,definition_uuid=row.role_uuid,at=database_now(),tag=tag)
        if consented:
            with transaction.atomic(),connection.cursor() as cur:
                _consent(cur,self.ids,nominee_identity_id=member.identity_id,nominee_membership_id=member.pk,
                    nominee_membership_uuid=member.membership_uuid,assignment_id=aid,assignment_uuid=auuid,
                    proposal_id=pid,at=database_now(),tag=tag)
        return member, models.ContextualRoleAssignment.objects.get(pk=aid), models.ContextualRoleAssignmentStateEvent.objects.get(pk=pid)

    def _consent_command(self,member,assignment,proposal,action):
        at=database_now()
        return consent_service.RecordOfficeConsentCommand(credential=member.identity.credential,
            request_reference="c4-consent:"+uuid4().hex,idempotency_key=uuid4().hex,occurred_at=at,effective_at=at,
            assignment_uuid=assignment.assignment_uuid,proposal_event_uuid=proposal.event_uuid,action=action)

    def test_02_proposals_apply_the_current_definition_rule_and_custom_gate(self):
        """O-07: temporary provider-only policy matrix, retained for SILENT entitlement, constitutional policy B and unsupported nomination-route boundaries until the complete service replacement is admitted."""
        nominee=self._member()
        # Constitutional member nomination is free; a non-Coordinator member acts.
        command=self._command()
        content=replace(command.content,nomination_basis="CONSTITUTION",nomination_policy="A",
            provisions=command.content.provisions+(definitions.DefinitionProvision("NOMINATION_ROUTE","library:test#nomination","v1"),))
        result=self._success(replace(command,content=content), action_description='Creating the board office with constitutional nomination policy A')
        row=models.OrganismRoleDefinition.objects.get(role_uuid=result.role_definition_uuid)
        facts=self._evaluate(self._proposal(row,nominee,actor_member=nominee), action_description='Putting yourself forward for the constitutional board office under policy A')
        self.assertEqual((facts["k"],facts["t"],facts["i"]),("A","SELF_PROPOSAL",None), msg='Putting yourself forward for this board office under constitutional policy A should record a free self-proposal with no nomination entitlement; the recorded facts differ.')
        self.assertNotIn("z",facts, msg='Putting yourself forward for this constitutional board office should not claim custom-role access; the decision incorrectly includes that entitlement.')
        unsupported=replace(content,nomination_policy="UNSUPPORTED",nomination_dependency="TEST committee route")
        amended=self._success(self._command("AMEND",row=row,content=unsupported), action_description='Changing that board office to its unsupported nomination route')
        # Naming the old version still uses the family's new rule, never the old A.
        self._evaluate(self._proposal(row,nominee,actor_member=nominee),"C_NOMINATION_ROUTE_NOT_IMPLEMENTED", action_description='Nominating yourself for the old board-office version after its current nomination route became unavailable')
        custom_command,custom=self._define_custom()
        facts=self._evaluate(self._proposal(custom,nominee), action_description='Nominating a member for the custom role under policy C as an effective Coordinator')
        self.assertEqual((facts["k"],facts["t"],facts["z"]),("C","EFFECTIVE_COORDINATOR","CUSTOM_ROLES"), msg='Nominating someone for a custom role under policy C should record the effective Coordinator and CUSTOM_ROLES access; the decision facts differ.')
        # Boundary fixture for a missing receipt entitlement, not grant/lapse lineage.
        with patch.object(definitions,"custom_role_entitled",return_value=False) as entitlement:
            self._evaluate(self._proposal(custom,nominee),"E_CUSTOM_ROLE_NOT_ENTITLED", action_description='Nominating a member for the custom role without custom-role access at receipt')
            self.assertEqual(entitlement.call_args.args[0],self.organism.organism_id, msg='Checking custom-role access for this nomination should use the organisation being acted in; the entitlement check used another organisation.')
            self.assertEqual(entitlement.call_args.args[2],"default", msg='Checking custom-role access for this nomination should read the normal writer database; the entitlement check used another database.')

        # Boundary fact-source fixture: entitlement evidence is supplied, then
        # absent; this does not witness subscription grant/lapse or installation.
        silent_command = self._command()
        silent_content = replace(silent_command.content, nomination_basis="SILENT", nomination_policy="A")
        entitlement_reference = str(uuid4())
        with patch.object(self.provider.nomination_entitlement_source, "reference_at", return_value=entitlement_reference):
            created = self._success(replace(silent_command, content=silent_content), action_description='Creating a board office under silent policy A with nomination entitlement evidence')
            silent = models.OrganismRoleDefinition.objects.get(role_uuid=created.role_definition_uuid)
            facts = self._evaluate(self._proposal(silent, nominee), action_description='Nominating another member under silent policy A with entitlement evidence')
        self.assertEqual((facts["k"], facts["t"], facts["i"]), ("A", "MEMBER_NOMINATION", entitlement_reference), msg='Nominating under silent policy A with entitlement should retain policy A and its evidence; the policy, route or entitlement differs.')
        with patch.object(self.provider.nomination_entitlement_source, "reference_at", return_value=None):
            facts = self._evaluate(self._proposal(silent, nominee), action_description='Nominating another member under silent policy A after entitlement evidence is unavailable')
        self.assertEqual((facts["k"], facts["t"], facts["i"]), ("C", "EFFECTIVE_COORDINATOR", None), msg='Nominating without silent-policy entitlement should fall back to policy C through an effective Coordinator with no entitlement claim; the decision facts differ.')



    def test_05_live_consent_refuses_stale_nomination_but_keeps_withdrawal(self):
        """O-22: fresh consent requires the current definition while withdrawal preserves the old chain."""
        command,row=self._define()
        m1,a1,p1=self._nomination(row)
        m2,a2,p2=self._nomination(row)
        service=consent_service.OfficeAppointmentConsentService(authority=self.authority,clock=database_now)
        first=service.record_office_consent(self._consent_command(m1,a1,p1,"CONSENT"))
        self.assertEqual((first.action,first.assignment_uuid),("CONSENT",a1.assignment_uuid), msg='Consenting to the current board-office nomination should record consent for that nomination; the returned action or nomination differs.')
        self._success(self._command("AMEND",row=row,content=command.content), action_description='Updating the board-office definition after the first nominee consented')
        before=self._snapshot()
        with self.assertRaises(consent_service.S015OfficeConsentSuperseded, msg='Consenting after the office definition was updated should refuse because a new nomination under the current definition is needed; the stale consent was not refused.') as stale:
            service.record_office_consent(self._consent_command(m2,a2,p2,"CONSENT"))
        self.assertEqual(stale.exception.code,"SR_SUPERSEDED_DEFINITION", msg='Refusing consent to an outdated nomination should identify the superseded office definition; a different refusal was returned.')
        self.assertEqual(stale.exception.support_class,"A", msg='Explaining that consent needs a new nomination under the current definition should use actionable support class A; another class was returned.')
        self.assertIs(stale.exception.retired, False, msg='Refusing consent after an office definition was updated should identify a superseded version, not a retired office; the retired flag differs.')
        self.assertEqual(stale.exception.support_message, consent_service.OFFICE_CONSENT_MESSAGES["SUPERSEDED"], msg='Refusing consent after a definition update should explain the need for a new nomination under the current version; the support message differs.')
        # Constructor only: this is not a live retired-office consent witness.
        self.assertEqual(str(consent_service.S015OfficeConsentSuperseded(retired=True)), consent_service.OFFICE_CONSENT_MESSAGES["RETIRED"], msg='Constructing the retired-office consent refusal should explain retirement and that appointment is unavailable; the constructor selected another explanation.')
        after=self._snapshot()
        self.assertEqual({k:v for k,v in after.items() if k!="CommandIntake"},{k:v for k,v in before.items() if k!="CommandIntake"},'Refusing stale consent should leave the nomination, decisions and consent history unchanged apart from the request record; other stored records changed.')
        withdrawn=service.record_office_consent(self._consent_command(m1,a1,p1,"WITHDRAW"))
        self.assertEqual(withdrawn.action,"WITHDRAW", msg='Withdrawing prior consent after the office definition was updated should record withdrawal; a different action was returned.')
        a1.refresh_from_db()
        self.assertEqual(a1.role_definition_id,row.pk, msg="Withdrawing consent should preserve the nomination's original office definition; the nomination points to a different version.")
        self.assertEqual(a1.state,"PROPOSED",'Withdrawing consent should leave the nomination proposed rather than close it; its state changed.')

    def test_06_participation_requirement_refuses_proposal_and_assignment(self):
        """SR §F.2: temporary negative participation witness; C5 positive/closed/ended replacement must qualify before this is shed."""
        command=self._command()
        content=replace(command.content,participation_required=True,
            provisions=command.content.provisions+(definitions.DefinitionProvision("PARTICIPATION_REQUIRED","library:test#participation","v1"),))
        result=self._success(replace(command,content=content), action_description='Creating the board office that requires GOVERNANCE participation')
        row=models.OrganismRoleDefinition.objects.get(role_uuid=result.role_definition_uuid)
        member,assignment,proposal=self._nomination(row,consented=True)
        self._evaluate(self._proposal(row,member),"E_NOMINEE_NOT_ELIGIBLE", action_description='Nominating a member without required GOVERNANCE participation for the board office')
        self._evaluate(self._request({"event_action":"ASSIGN","assignment_uuid":str(assignment.assignment_uuid)}),"E_NOMINEE_NOT_ELIGIBLE", action_description='Appointing a member without required GOVERNANCE participation to the board office')
        # Real positive SB-G participation and Circle lifecycle are named cuts;
        # source review of the query is not a runtime positive witness.

    def test_07_coordinator_qualification_remains(self):
        """O-18: Coordinator qualification retains its original policy and provenance."""
        member=models.OrganismMembership.objects.select_related("identity__credential","head_transition").get(pk=self.ids["membership"])
        role=models.OrganismRoleDefinition.objects.get(pk=self.ids["roles"]["COORDINATOR"])
        facts=self._evaluate(self._proposal(role,member,actor_member=member), action_description='Putting yourself forward for Coordinator under its existing nomination policy')
        self.assertEqual((facts["k"],facts["t"],facts["i"]),("C","SELF_PROPOSAL",None), msg='Putting yourself forward for Coordinator should retain policy C and the self-proposal route without nomination entitlement; the Coordinator facts differ.')
        self.assertTrue("c" in facts or "d" in facts, msg='Putting yourself forward for Coordinator should name either the recorded governance settings or the default nomination policy; both are missing.')
        self.assertNotIn("z",facts, msg='Putting yourself forward for Coordinator should not require custom-role access; the decision incorrectly includes it.')
        self.assertNotIn("e",facts, msg='Putting yourself forward for Coordinator should not add a GOVERNANCE participation requirement; the decision incorrectly includes one.')


    def test_08_reserved_office_consent_actions_refuse_at_the_live_provider(self):
        """O-14 route restriction: all three live consent acts refuse a reserved office before a write.

        Fixture latitude: a committed, lawful BOARD_OFFICE nomination is read as
        INTEVIA_TEST_RESERVED for this one definition PK only. This is a scoped
        model-read substitute, not a disabled safeguard, fake decision, or patch
        to the provider. All intake, authority and consent writers remain real.
        It witnesses the prefix refusal on the live route, not a stored reserved
        definition, root founding or the plan's historical root-LO scaffold.
        SF11 removes only that provider refusal; the underlying lawful nomination
        then permits each challenged consent action, so assertRaises must FAIL.
        """
        command, row = self._define()
        stored_code = models.OrganismRoleDefinition.objects.values_list("code", flat=True).get(pk=row.pk)
        service = consent_service.OfficeAppointmentConsentService(authority=self.authority, clock=database_now)
        original_getattribute = models.OrganismRoleDefinition.__getattribute__
        def reserved_fixture(instance, name):
            if name == "code" and original_getattribute(instance, "pk") == row.pk:
                return "INTEVIA_TEST_RESERVED"
            return original_getattribute(instance, name)
        for action in ("CONSENT", "DECLINE", "WITHDRAW"):
            with self.subTest(action=action, organisation=str(self.organism.organism_id), office="INTEVIA_TEST_RESERVED"):
                member, assignment, proposal = self._nomination(row, consented=action == "WITHDRAW")
                request = self._consent_command(member, assignment, proposal, action)
                before = self._snapshot()
                with patch.object(models.OrganismRoleDefinition, "__getattribute__", reserved_fixture):
                    with self.assertRaises(S015AuthorityDenied, msg='Responding to a nomination for a reserved INTEVIA office should refuse because INTEVIA manages that office; the consent action was not refused.') as refused:
                        service.record_office_consent(request)
                self.assertEqual(refused.exception.disclosed_refusal_code.value, "C_RESERVED_ROOT_OFFICE", msg='Refusing a consent action for a reserved INTEVIA office should identify the reserved-office rule; another refusal was returned.')
                self.assertIsNotNone(refused.exception.authority_decision_reference, msg='Refusing a consent action for a reserved INTEVIA office should return the decision reference; that reference is missing.')
                self.assertEqual(refused.exception.support_class, "B", msg='Explaining that INTEVIA manages this reserved office should use support class B; another class was returned.')
                from src.intevia.services.organism_role_service import OFFICE_ACTION_NAMES
                self.assertEqual(refused.exception.support_message, consent_service.OFFICE_CONSENT_MESSAGES["C_RESERVED_ROOT_OFFICE"].format(action=OFFICE_ACTION_NAMES[action]), msg='Refusing a reserved-office consent action should explain that INTEVIA manages the office and there is nothing for the person to do; the displayed explanation differs.')
                after = self._snapshot()
                self.assertEqual({k:v for k,v in after.items() if k not in {"CommandIntake", "AuthorityDecision"}}, {k:v for k,v in before.items() if k not in {"CommandIntake", "AuthorityDecision"}}, msg='Refusing a reserved-office consent action should leave business records and command receipts unchanged; a record outside the request and refusal changed.')
                self.assertEqual(len(after["CommandIntake"]), len(before["CommandIntake"]) + 1, msg='Refusing each reserved-office consent action should retain exactly one new request record; the request count differs.')
                self.assertEqual(len(after["AuthorityDecision"]), len(before["AuthorityDecision"]) + 1, msg='Refusing each reserved-office consent action should retain exactly one new authority decision; the decision count differs.')
        self.assertEqual(models.OrganismRoleDefinition.objects.get(pk=row.pk).code, stored_code, msg='Finishing all reserved-office consent checks should restore the original stored office code; the definition read differs from the code recorded before the substitution.')
        self.assertNotEqual(models.OrganismRoleDefinition.objects.get(pk=row.pk).code, "INTEVIA_TEST_RESERVED", msg='Finishing all reserved-office consent checks should remove the test-only reserved-office substitution; the definition still reads as the substituted reserved office.')
