"""ELECT-06–08. Scoped grants, open ballots and distinct vacancy routes."""

import json
from dataclasses import replace
from uuid import uuid4

from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_commands as circles
from src.intevia.services import cq, cq_reads, elections, law_acts, library_policy
from src.intevia.services import discussion as disc
from src.intevia.services.organism_office_consent_service import (
    OfficeAppointmentConsentService,
)
from src.intevia.services.organism_role_service import OrganismRoleService
from tests import cq_authority_support as board
from tests import cq_settings_support
from tests import election_support as fx
from tests import s015_0021_support as support
from tests import test_cq_clarity as clarity
from tests import test_law_acts as law
from tests import test_sr_office_actions as office


class ElectionMandateTests(TransactionTestCase):
    topic = clarity.CQClarityTests.topic

    def board(self, *, empty_seat=False):
        lo, founder = law.ordinary_birth(
            offering_choice="COMPLIMENTARY_BETA",
            board_seats=4 if empty_seat else 3,
            elected=(3,) if empty_seat else (),
        )
        return self.activate_board(lo, founder)

    def activate_board(self, lo, founder, activation_probe=None):
        circle = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        general = {"organism_uuid": lo.organism_id, "credential": founder.credential}
        fx.enable_voting(general)
        chair = board.seat_trustee(lo, founder, circle, 1)
        third = board.seat_trustee(lo, founder, circle, 2)
        activation = general | {"circle_uuid": circle.circle_uuid, "request_uuid": uuid4()}
        if activation_probe is None:
            circles.activate(**activation)
        else:
            activation_probe(activation)
        board.appoint_board_coordinator(lo, circle, founder, chair)
        return (
            lo,
            general,
            general | {"circle_uuid": circle.circle_uuid, "credential": chair.credential},
            [founder, chair, third],
        )

    def decision(self, scope, attachment, voters):
        journey, _, topic = self.topic(
            scope, title="Decide this exact instrument", markdown=attachment["text"]
        )
        cq.put_forward(**journey, version_uuid=topic["version"], attachment=attachment, request_uuid=uuid4())
        ballot = fx.open_election(journey)
        for voter in voters:
            fx.cast(journey, ballot, voter)
        result = cq_reads.vote_status(**journey)["result_uuid"]
        record = cq_reads.implementation_record(**journey, result_uuid=result)
        self.assertIsNotNone(record["effect"], "The competent body must implement the exact instrument.")
        return result, record["effect"]

    def constitution(self, lo, founder, general, voters, body):
        doc = law.Document(founder)
        version = doc.service.create_successor_version(
            identity=doc.author,
            resource_id=doc.name,
            content=json.dumps(body),
            evidence_reference="fixture:elect:constitutional-text",
        )
        doc.transition("publish_library_resource")
        proposal = replace(law.adoption(lo, doc, "ARTICLE"), version=version.version_number)
        result, _ = self.decision(
            general, {"kind": "LAW", "text": version.content, "spec": proposal.content()}, voters
        )
        return {"binding": str(proposal.proposal), "version": version.pk}, result

    def policy(self, lo, general, governed, trustees, wording):
        # The Board publishes the exact authored text, then separately decides its adoption.
        resource = "elections-policy-" + uuid4().hex
        version = library_policy.draft_policy(
            **general,
            resource_reference=resource,
            content=wording,
            authors=[trustees[0].identity_id],
            declare_own_authorship=True,
            request_uuid=uuid4(),
        )
        self.decision(
            governed,
            {
                "kind": "PUBLISH_POLICY",
                "spec": {
                    "organism": str(lo.organism_id),
                    "version": version.version_number,
                    "resource": resource,
                },
                "text": version.content,
            },
            trustees,
        )
        proposal = law_acts.LawProposal(
            organism=lo.organism_id,
            resource=resource,
            proposal=uuid4(),
            operation="ADOPT",
            intent="ADOPT",
            law_class="POLICY",
            version=version.version_number,
        )
        return self.decision(
            governed, {"kind": "LAW", "spec": proposal.content(), "text": version.content}, trustees
        )

    def committee(self, scope):
        return circles.form(
            **scope,
            circle_uuid=uuid4(),
            request_uuid=uuid4(),
            kind="GENERIC",
            name="Nominations and Elections",
            purpose="Administer only the election powers the Board grants.",
        )

    def hand_over_committee_office(self, lo, scope, committee, founder, successor):
        member = models.OrganismMembership.objects.get(living_organism=lo, identity=successor)
        local = scope | {"circle_uuid": committee.circle_uuid}
        req = circles.request_place(
            **(local | {"credential": successor.credential}),
            membership_uuid=member.membership_uuid,
            request_uuid=uuid4(),
            consent="I request this committee place.",
        )
        place = circles.admit(**local, request_uuid=req.request_uuid)
        roles = OrganismRoleService(authority=board.authority(), clock=support.database_now)
        nomination, key = uuid4(), uuid4()
        roles.nominate_circle_coordinator(
            **local, participation_uuid=place.participation_uuid, assignment_uuid=key, request_uuid=nomination
        )
        appointment = models.ContextualRoleAssignment.objects.get(assignment_uuid=key)
        helper = office.SROfficeActionTests()
        OfficeAppointmentConsentService(
            authority=board.authority(), clock=support.database_now
        ).record_office_consent(helper._consent_command(member, appointment))
        roles.appoint_circle_coordinator(**local, assignment_uuid=key, request_uuid=uuid4())
        old = models.CircleParticipation.objects.get(circle=committee, membership__identity=founder)
        circles.leave(
            **local, participation_uuid=old.participation_uuid, request_uuid=uuid4(), confirmed=True
        )

    def test_committee_grant_and_return_move_only_the_named_election(self):
        """ELECT-06. A competent Board grant and return move only administration of one member-elected place."""
        from tests import committee_support as c

        self.topic_category_ids = {}
        lo, general, governed, trustees = self.board(empty_seat=True)
        founder, chair, _ = trustees
        candidate = law.add_member(lo)
        members = [*trustees, candidate]
        committee = self.committee(general)
        self.hand_over_committee_office(lo, general, committee, founder, candidate)
        c.join(general | {"credential": candidate.credential}, committee, chair)
        recipient = str(committee.circle_uuid)
        citation, permission = self.constitution(
            lo,
            founder,
            general,
            members,
            {
                "committees": {"chair_board_member": False},
                "elections": {"Trustee": fx.seat(place="trustee-3")},
                "authority_transfers": {
                    "Delegation": {
                        "grantor": "GOVERNANCE",
                        "power": "RUN_ELECTION",
                        "election": "Trustee",
                        "recipients": [recipient, "LO_MEMBERSHIP"],
                    }
                },
            },
        )
        scope = citation | {"power": "RUN_ELECTION", "provision": "Trustee", "place": "trustee-3"}

        def wording(before, after, predecessor):
            return elections.transfer_policy(
                scope=scope,
                citation=citation | {"provision": "Delegation"},
                from_body=before,
                to_body=after,
                predecessor=predecessor,
                purpose="Form the Nominations & Elections committee to administer the trustee election.",
            )

        # The named stand-in is also standing in this contest. The Board must name another.
        _, forming = c.mandate(lo, founder, committee, "2", chair=candidate, stand_in=str(candidate.identity_id))
        adoption = json.loads(wording("LO_MEMBERSHIP", recipient, None)) | {
            "committee": forming, "election_powers": ["CALL_NOMINATIONS", "PREPARE_SLATE", "RUN_ELECTION"]}
        decision, effect = self.policy(
            lo, general, governed, trustees, json.dumps(adoption)
        )
        self.assertEqual(effect["committee"]["stand_in"], str(candidate.identity_id))
        history = elections.power_history(**general, scope=scope)
        self.assertEqual(
            [(h["result"], h["ilc_event"], h["membership_decision"], h["predecessor"]) for h in history],
            [(decision, effect["implementation"], str(permission), None)],
        )
        self.assertEqual(elections.power_history(**general, scope={"power": "VOTING_SETTINGS"}), [])
        rule = {"binding_uuid": citation["binding"], "version_id": citation["version"]}
        with self.assertRaisesMessage(cq.CQRefused, cq.MESSAGES["ELECT_CALL"]):
            fx.call(self, general, rule, provision="Trustee", place="trustee-3")
        administered = general | {"credential": candidate.credential}
        with self.assertRaisesMessage(cq.CQRefused, cq.MESSAGES["CQ_BODY"]):
            fx.call(self, governed, rule, provision="Trustee", place="trustee-3")
        journey = fx.call(self, administered, rule, provision="Trustee", place="trustee-3")
        fx.nominate(journey, candidate)
        with self.assertRaisesMessage(cq.CQRefused, cq.MESSAGES["CMT_RECUSED"]):
            cq.mark_prepared(**journey, request_uuid=uuid4())
        stand_in = c.step(governed, effect["committee"]["binding"], "NAME_ADMIN",
                          contest=str(journey["thread_uuid"]), administrator=str(founder.identity_id))
        self.decision(governed, stand_in, trustees)
        ballot = fx.open_election(journey | {"credential": founder.credential})
        self.assertEqual(cq_reads.vote_status(**journey)["eligible"], 4)
        for member in members:
            fx.cast(journey, ballot, member)
        win = cq_reads.vote_status(**journey)["result_uuid"]
        seat = models.CircleParticipation.objects.get(circle__parent_organism=lo, seat_id="trustee-3")
        basis = elections.authorising_result(**general, event_uuid=seat.participation_uuid)
        self.assertEqual(
            (basis["result"], basis["person"], basis["electorate"], basis["administration"]["transfer"]),
            (win, str(candidate.identity_id), "LO_MEMBERSHIP", str(effect["implementation"])),
        )
        returned, later = self.policy(
            lo,
            general,
            governed,
            trustees + [candidate],
            wording(recipient, "LO_MEMBERSHIP", effect["implementation"]),
        )
        history = elections.power_history(**general, scope=scope)
        self.assertEqual(
            (history[-1]["result"], history[-1]["predecessor"], history[-1]["holder"]),
            (returned, str(effect["implementation"]), "LO_MEMBERSHIP"),
        )
        event = models.LivingOrganismEvent.objects.get(event_uuid=later["implementation"])
        self.assertEqual(event.evidence_reference, f"elect-transfer:{returned}:{effect['implementation']}")
        with self.assertRaisesMessage(cq.CQRefused, cq.MESSAGES["ELECT_CALL"]):
            fx.call(self, administered, rule, provision="Trustee", place="trustee-3")

    def test_settings_ballot_keeps_its_opening_mandate_during_transfer(self):
        """ELECT-07. The original membership can finish its open decision after settings pass to a Circle."""
        self.topic_category_ids = {}
        lo, general, governed, trustees = self.board()
        committee = self.committee(general)
        local = general | {"circle_uuid": committee.circle_uuid}
        disc.ensure_board(**local, request_uuid=uuid4())
        recipient = str(committee.circle_uuid)
        citation, _ = self.constitution(
            lo,
            trustees[0],
            general,
            trustees,
            {
                "authority_transfers": {
                    "Voting": {"grantor": "GOVERNANCE", "power": "VOTING_SETTINGS", "recipients": [recipient]}
                },
            },
        )
        before = cq.settings(**general)["version"]
        pending, _ = cq_settings_support.propose(self, general, {"abstention": True}, before)
        ballot = fx.open_election(pending)
        fx.cast(pending, ballot, trustees[0])
        policy, effect = self.policy(
            lo,
            general,
            governed,
            trustees,
            elections.transfer_policy(
                scope={"power": "VOTING_SETTINGS"},
                citation=citation | {"provision": "Voting"},
                from_body="LO_MEMBERSHIP",
                to_body=recipient,
                predecessor=None,
                purpose="Give the committee responsibility for voting settings.",
            ),
        )
        for voter in trustees[1:]:
            fx.cast(pending, ballot, voter)
        result = cq_reads.vote_status(**pending)["result_uuid"]
        proof = cq_reads.implementation_record(**pending, result_uuid=result)["effect"]
        self.assertEqual(
            (proof["authority_source"]["route"], cq.settings(**general)["settings"]["abstention"]),
            ("CQ_LO_MEMBERSHIP", True),
        )
        with self.assertRaisesMessage(cq.CQRefused, cq.MESSAGES["CQ_BODY"]):
            cq_settings_support.propose(
                self, general, {"abstention": False}, cq.settings(**general)["version"]
            )
        latest = elections.power_history(**general, scope={"power": "VOTING_SETTINGS"})[-1]
        self.assertEqual(
            (latest["result"], latest["transfer"], latest["holder"]),
            (policy, effect["implementation"], recipient),
        )
        # With one eligible committee member, DISC4's existing below-three route belongs to that Circle's office.
        change = cq.change_settings(
            **general,
            changes={"abstention": False},
            predecessor=cq.settings(**general)["version"],
            request_uuid=uuid4(),
        )
        self.assertEqual(cq.settings(**general)["version"], change)

    def test_vacancy_starts_one_cited_route_and_holds_constitutional_silence(self):
        """ELECT-08. C2 office ending, active Board co-option, retained trustee place and a silent constitution."""
        lo, founder = law.ordinary_birth(offering_choice="COMPLIMENTARY_BETA", board_seats=3)
        terms = {
            label: {"type": "OFFICE", "place": label.upper(), "body": "GOVERNANCE"}
            for label in ("Treasurer", "Secretary")
        }
        vacancies = {"Interim Treasurer": {
            "type": "OFFICE", "place": "TREASURER", "route": "COOPTION",
            "authority": "GOVERNANCE", "election": "Treasurer",
        }}
        rule = fx.provision(founder, lo, terms, vacancies=vacancies)
        offices = {label: fx.define_office(lo, founder, label.upper(), "GOVERNANCE", label, rule,
                                          board_members=True) for label in terms}
        _, general, governed, trustees = self.activate_board(lo, founder)
        _, chair, third = trustees
        roles = OrganismRoleService(authority=board.authority(), clock=support.database_now)
        helper = office.SROfficeActionTests()
        helper.actor, helper.organism = founder, lo
        for label in ("Treasurer", "Secretary"):
            place = label.upper()
            assignment = fx.office_proposal(lo, founder, third, offices[label], self_proposed=True)
            journey = fx.call(self, governed, rule, provision=label, place=place)
            fx.nominate(journey, third, assignment=assignment.assignment_uuid)
            ballot = fx.open_election(journey)
            for voter in trustees:
                fx.cast(journey, ballot, voter)
            if label == "Treasurer":
                models.EssentialCoverageRequirement.objects.create(
                    living_organism=lo,
                    roster_version=1,
                    responsibility_domain_code="TREASURY",
                    qualifying_role_code=place,
                    scope=assignment.role_definition.scope,
                    minimum_active_occupants=1,
                    effective_from=support.database_now(),
                )
            roles.transition_assignment(helper._transition_command(assignment, "END", actor=third))
            end = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=assignment, action="END")
            (record,) = elections.vacancies(**general, ending_uuid=end.event_uuid)
            self.assertFalse(
                models.CircleParticipationEnd.objects.filter(
                    participation__circle__parent_organism=lo, participation__membership__identity=third
                ).exists()
            )
            self.assertFalse(elections.catch_up_vacancies(credential=founder.credential))
            spec = elections.attachment(
                organism_uuid=lo.organism_id,
                **rule,
                provision=label,
                place=place,
                vacancy_uuid=end.event_uuid,
            )
            call, _, topic = self.topic(governed, title="Fill the recorded vacancy", markdown=spec["text"])
            call_args = dict(
                **call,
                version_uuid=topic["version"],
                request_uuid=uuid4(),
                **rule,
                provision=label,
                place=place,
                vacancy_uuid=end.event_uuid,
            )
            if label == "Secretary":
                self.assertEqual(
                    (record["instruction"]["route"], record["call"], record["filled_by"]),
                    ("HOLD_HG", None, None),
                )
                with self.assertRaisesMessage(cq.CQRefused, cq.MESSAGES["ELECT_VACANCY_HOLD"]):
                    elections.call(**call_args)
                continue
            assessment = models.CoverageAssessment.objects.get(
                case__living_organism=lo, evidence_reference=f"office:{end.event_uuid}"
            )
            self.assertEqual(
                (
                    record["instruction"]["assessment"],
                    record["instruction"]["route"],
                    record["instruction"]["authority"],
                ),
                (str(assessment.event_uuid), "COOPTION", "GOVERNANCE"),
            )
            initiation = elections.call(**call_args)
            self.assertEqual(elections.call(**call_args), initiation)
            again, _, other = self.topic(
                governed, title="Second call for this vacancy", markdown=spec["text"]
            )
            with self.assertRaisesMessage(cq.CQRefused, cq.MESSAGES["ELECT_VACANCY_USED"]):
                elections.call(
                    **(call_args | again | {"version_uuid": other["version"], "request_uuid": uuid4()})
                )
            member = models.OrganismMembership.objects.get(living_organism=lo, identity=chair)
            proposed = roles.propose_assignment(
                helper._proposal_command(assignment.role_definition, member, actor=chair)
            )
            fx.nominate(call, chair, assignment=proposed.assignment_uuid)
            ballot = fx.open_election(call)
            for voter in trustees:
                fx.cast(call, ballot, voter)
            outcome = cq_reads.vote_status(**call)["result_uuid"]
            (filled,) = elections.vacancies(**general, ending_uuid=end.event_uuid)
            self.assertEqual((filled["call"], filled["result"]), (initiation, outcome))
            self.assertEqual(cq_reads.result_post(**call, result_uuid=outcome)["event_kind"], "DECISION")
            self.assertTrue(
                models.LivingOrganismEvent.objects.filter(
                    event_uuid=record["route_act"], action="RECORD_CQ"
                ).exists()
            )
            basis = elections.authorising_result(**general, event_uuid=filled["filled_by"])
            self.assertEqual(
                (basis["result"], basis["place"], basis["person"]), (outcome, place, str(chair.identity_id))
            )
