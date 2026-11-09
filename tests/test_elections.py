"""ELECT election, result binding and term continuity through the governed writers."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_commands, cq, cq_reads, elections
from src.intevia.services import discussion as disc
from src.intevia.services import discussion_posts as posts
from tests import cq_authority_support as board
from tests import election_support as fx
from tests import s015_0021_support as support
from tests import test_cq_clarity as clarity
from tests import test_law_acts as law


class ElectionTests(TransactionTestCase):
    topic = clarity.CQClarityTests.topic

    def test_member_election_seats_trustee_before_activation(self):
        """ELECT-01. A cited member vote fills an elected seat while the Board is dormant. Uncited and unsupported calls refuse."""
        term_end = support.database_now() + timedelta(days=90)
        rules = {
            fx.ARTICLE: fx.seat(term={"end": term_end.isoformat()}),
            "Joint": fx.seat(counting={"method": "PREFERENTIAL"}),
            "Undated": fx.seat(term={"end": "next term"}),
        }
        lo, founder, scope, circle, citation = fx.owner(rules=rules)
        trustee, third = law.add_member(lo), law.add_member(lo)
        for provision, expected in [
            ("Missing", cq.MESSAGES["ELECT_CITATION"]),
            ("Joint", "This counting method is unsupported. Hold the election before opening."),
            ("Undated", "Review the term. Supply an end and any start with a time zone, with the end after the start or current time."),
        ]:
            with self.subTest(provision=provision):
                with self.assertRaises(cq.CQRefused) as refused:
                    fx.call(self, scope, citation, provision=provision)
                self.assertEqual(str(refused.exception), expected)
        election = fx.call(self, scope, citation)
        nomination = fx.nominate(election, trustee)
        ballot = fx.open_election(election)
        for person in [founder, trustee, third]:
            fx.cast(election, ballot, person)
        result = cq_reads.vote_status(**election)["result"]
        self.assertEqual((result["count"]["kind"], result["count"]["winner"]), ("QUALIFIED", str(trustee.identity_id)))
        effect = cq_reads.implementation_record(**election, result_uuid=result["post_uuid"])["effect"]
        self.assertEqual(effect["election"]["consent"], str(nomination))
        place = models.CircleParticipation.objects.select_related("decision").get(circle=circle, seat_id="trustee-1")
        self.assertEqual((place.membership.identity_id, place.decision.decision_uuid, place.decision.deciding_identity_id),
                         (trustee.pk, effect["implementation"], None))
        self.assertEqual(place.decision.deciding_party, "body:LO_MEMBERSHIP")
        ending = models.CircleParticipationEnd.objects.get(participation=place)
        self.assertEqual((ending.effective_at, ending.composed_from_event_uuid), (term_end, effect["implementation"]))
        self.assertEqual(ending.composing_rule_reference, "elect-term:" + str(result["post_uuid"]))
        circle.refresh_from_db()
        self.assertIn(circle.state, ("DORMANT", "ELIGIBLE"))

    def test_office_election_follows_constitutional_body_and_consent(self):
        """ELECT-02. Direct-member and Board elections use their own electorate and exact consenting office proposal."""
        rules = {"Treasurer": {"type": "OFFICE", "place": "TREASURER", "body": "LO_MEMBERSHIP"},
                 "Chair": {"type": "OFFICE", "place": "CHAIR", "body": "GOVERNANCE"}}
        lo, founder, scope, circle, citation = fx.owner(seats=3, elected=(), rules=rules, voting=False)
        offices = {
            section: fx.define_office(lo, founder, rule["place"], rule["body"], section, citation,
                                      board_members=rule["body"] == "GOVERNANCE")
            for section, rule in rules.items()
        }
        fx.enable_voting(scope)
        second = board.seat_trustee(lo, founder, circle, 1)
        third = board.seat_trustee(lo, founder, circle, 2)
        member = law.add_member(lo)
        circle_commands.activate(**scope, circle_uuid=circle.circle_uuid, request_uuid=uuid4())
        board.appoint_board_coordinator(lo, circle, founder, second)
        for section, voters, circle_scope in [("Treasurer", [founder, second, third, member], {}),
                                               ("Chair", [founder, second, third], {"circle_uuid": circle.circle_uuid, "credential": second.credential})]:
            with self.subTest(section=section):
                rule = rules[section]
                candidate = third if circle_scope else second
                assignment = fx.office_proposal(lo, founder, candidate, offices[section], self_proposed=bool(circle_scope))
                election = fx.call(self, scope | circle_scope, citation, provision=section, place=rule["place"])
                nom = fx.nominate(election, candidate, by=None if circle_scope else founder, assignment=assignment.assignment_uuid)
                if not circle_scope:
                    with self.assertRaises(cq.CQRefused) as refused:
                        fx.open_election(election)
                    self.assertEqual(str(refused.exception), cq.MESSAGES["ELECT_EMPTY"])
                    elections.consent(**(election | {"credential": second.credential}), request_uuid=uuid4(), nomination_uuid=nom)
                ballot = fx.open_election(election)
                self.assertEqual(cq_reads.vote_status(**election)["eligible"], len(voters))
                for person in voters:
                    fx.cast(election, ballot, person)
                post = cq_reads.vote_status(**election)["result"]
                effect = cq_reads.implementation_record(**election, result_uuid=post["post_uuid"])["effect"]
                event = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=assignment, action="ASSIGN")
                self.assertEqual(event.evidence_reference, "elect-result:" + str(post["post_uuid"]))
                self.assertEqual((event.event_uuid, event.resulting_state), (effect["implementation"], "ACTIVE"))
                self.assertEqual(effect["election"]["body"], rule["body"])

    def test_count_requires_turnout_then_a_unique_qualified_candidate(self):
        """ELECT-03. Default floor, constitutional denominator, abstentions, ties, three-way split and the below-three stop."""
        people = [str(uuid4()) for _ in range(3)]
        a, b, c = people
        slate = [{"person": p} for p in people]
        rules = {"quorum_form": "MAJORITY", "threshold": "MAJORITY", "threshold_base": "VOTES_CAST"}
        cases = [
            (7, {a: 3}, {}, "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", None),
            (2, {a: 2}, {}, "NO_DECISION_FEWER_THAN_THREE", None),
            (7, {a: 2, b: 2, c: 1}, {}, "NOT_QUALIFIED_TIE", None),
            (7, {a: 3, b: 2, c: 1}, {}, "NOT_QUALIFIED_VOTE", None),
            (7, {a: 3, b: 1, "ABSTAIN": 2}, {}, "QUALIFIED", a),
            (7, {a: 3, b: 1, "ABSTAIN": 2}, {"threshold_base": "PARTICIPANTS"}, "NOT_QUALIFIED_VOTE", None),
        ]
        import json
        for eligible, totals, override, kind, winner in cases:
            with self.subTest(eligible=eligible, totals=totals, basis=override):
                count = disc._rows("SELECT public.elect_tally(%s::jsonb,%s::jsonb,%s::jsonb,%s) AS tally",
                    [json.dumps(rules | override), json.dumps(slate), json.dumps(totals), eligible])[0]["tally"]
                self.assertEqual((count["kind"], count["winner"], count["participated"]), (kind, winner, sum(totals.values())))

        # Start with the platform's own rule. Vary one constitutional instruction per case.
        for values, expected in [
            ({"abstention": "yes"}, "The electoral abstention rule must be true or false."),
            ({"quorum_form": "PERCENT", "quorum_value": 101},
             "Review the electoral participation rule, its value and any minimum waiver in the cited provision."),
            ({"threshold": "FRACTION", "numerator": 3, "denominator": 2},
             "Review the qualification threshold and any fraction in the cited electoral provision."),
        ]:
            with self.subTest(instruction=values):
                with self.assertRaises(cq.CQRefused) as refused:
                    try:
                        with transaction.atomic(), connection.cursor() as cur:
                            cur.execute(
                                "SELECT public.elect_check_count(settings || %s::jsonb, %s::jsonb, statement_timestamp()) "
                                "FROM public.cq_platform_defaults",
                                [json.dumps(values), json.dumps(values)],
                            )
                    except IntegrityError as exc:
                        cq.translate_refusal(exc)
                self.assertEqual(str(refused.exception), expected)

    def test_secret_receipts_and_withdrawal_across_reads(self):
        """ELECT-04. Secret per-voter receipts and aggregate reads survive settled withdrawal. No appointment or automatic tie rerun."""
        lo, founder, scope, circle, citation = fx.owner()
        second, third = law.add_member(lo), law.add_member(lo)
        journey = fx.call(self, scope, citation)
        nom = fx.nominate(journey, second)
        fx.nominate(journey, third)
        ballot = fx.open_election(journey)
        receipt = fx.cast(journey, ballot, founder, second.identity_id)
        self.assertEqual(fx.cast(journey, ballot, founder, second.identity_id, receipt), receipt)
        self.assertEqual(cq_reads.vote_status(**journey)["own_receipt"], receipt)
        viewer = journey | {"credential": third.credential}
        outsider = support.register_test_learner(username="elect-outsider-" + uuid4().hex)
        with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
            elections.slate(**(journey | {"credential": outsider.credential}))
        status = cq_reads.vote_status(**viewer)
        self.assertEqual((status["participated"], status["own_receipt"]), (1, None))
        self.assertNotIn("participation", status)
        facts = disc._rows("SELECT request,facts FROM public.core_cqact WHERE act_uuid=%s", [receipt])[0]
        self.assertNotIn("choice", facts["request"])
        self.assertNotIn(str(second.identity_id), str(facts))
        elections.withdraw(**(journey | {"credential": second.credential}), nomination_uuid=nom, request_uuid=uuid4())
        post = cq_reads.vote_status(**viewer)["result"]
        self.assertEqual((post["count"]["kind"], post["count"]["winner"], post["restarted"]), ("NO_DECISION_SLATE_CHANGED", None, False))
        result = cq_reads.result_post(**viewer, result_uuid=post["post_uuid"])
        self.assertNotIn("participants", result)
        self.assertEqual(result["event_kind"], "ELECTION")
        self.assertEqual(cq_reads.reporting_decision(**viewer, result_uuid=post["post_uuid"])["origin"], "ELECTION")
        self.assertIsNone(cq_reads.implementation_record(**viewer, result_uuid=post["post_uuid"])["effect"])
        shared_post = next(p for p in posts.read_thread(**viewer)["posts"] if p.get("post_uuid") == result["post_uuid"])
        self.assertEqual(shared_post["count"], result["count"])
        self.assertNotIn("participants", shared_post)
        fresh = fx.call(self, scope, citation)
        fx.nominate(fresh, third)
        fresh_ballot = fx.open_election(fresh)
        for person in (founder, second, third):
            fx.cast(fresh, fresh_ballot, person, "NOT_QUALIFIED")
        defeated = cq_reads.vote_status(**fresh)["result"]
        self.assertEqual((defeated["count"]["kind"], defeated["count"]["winner"]), ("NOT_QUALIFIED_VOTE", None))
        self.assertIsNone(cq_reads.implementation_record(**fresh, result_uuid=defeated["post_uuid"])["effect"])
        self.assertFalse(models.CircleParticipation.objects.filter(circle=circle, seat_id="trustee-1").exists())

    def test_result_authorises_exact_person_and_place(self):
        """ELECT-05. An actual result binds winner, place and term. Refuse substitutions and reuse the same receipt/effect."""
        term_end = support.database_now() + timedelta(days=1)
        lo, founder, scope, circle, citation = fx.owner(rules={fx.ARTICLE: fx.seat(term={"end": term_end.isoformat()})})
        winner, other = law.add_member(lo), law.add_member(lo)
        journey = fx.call(self, scope, citation)
        fx.nominate(journey, winner)
        ballot = fx.open_election(journey)
        for person in (founder, winner):
            fx.cast(journey, ballot, person)
        receipt = fx.cast(journey, ballot, other)
        result = cq_reads.vote_status(**journey)["result_uuid"]
        effect = cq_reads.implementation_record(**journey, result_uuid=result)["effect"]
        start = models.CircleParticipation.objects.get(participation_uuid=effect["implementation"]).effective_at
        def authorised(person, place, at, end=term_end):
            with connection.cursor() as cur:
                cur.execute("SELECT public.elect_authorises(%s,%s,%s,%s,%s,%s)", [result, lo.pk, person.identity_id, place, at, end])
                return cur.fetchone()[0]
        self.assertTrue(authorised(winner, "trustee-1", start))
        self.assertFalse(authorised(other, "trustee-1", start), "The result must authorise the exact elected person and place.")
        self.assertFalse(authorised(winner, "trustee-1", start, term_end + timedelta(microseconds=1)),
                         "A different end must not be authorised for the same winner, seat and start.")
        self.assertFalse(authorised(winner, "trustee-1", start, None),
                         "An open-ended appointment cannot be substituted for the fixed winning term.")
        self.assertFalse(authorised(winner, "trustee-0", start))
        self.assertFalse(authorised(winner, "trustee-1", start + timedelta(seconds=1)))
        self.assertEqual(fx.cast(journey, ballot, other, request=receipt), receipt)
        self.assertEqual(cq_reads.implementation_record(**journey, result_uuid=result)["effect"], effect)
        self.assertEqual(models.CircleParticipation.objects.filter(circle=circle, seat_id="trustee-1").count(), 1)
        with self.assertRaises(cq.CQRefused) as forged, transaction.atomic():
            try:
                with connection.cursor() as cur:
                    cur.execute("""INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,
                        actor_epoch,received_at,request) SELECT gen_random_uuid(),organism_id,board_uuid,thread_uuid,
                        'IMPLEMENT',actor_id,actor_epoch,statement_timestamp(),jsonb_build_object('result',act_uuid)
                        FROM public.core_cqact WHERE act_uuid=%s""", [result])
            except IntegrityError as exc:
                cq.translate_refusal(exc)
        self.assertEqual(str(forged.exception), cq.MESSAGES["CQ_COMPLETE"])

    def test_hard_storage_applies_the_constitutional_seat_route_at_birthing(self):
        """HARD-H2. A stored admission must use the route available for that exact seat at its cut."""
        from src.intevia.services import organism_governance_service as governance

        lo, founder = law.ordinary_birth(board_seats=4, elected=(2,), external=(3,))
        circle = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        founding = models.CircleParticipation.objects.select_related("decision").get(
            circle=circle, membership__identity=founder)
        self.assertEqual(founding.decision.route, "CIRCLE_FOUNDING")

        # A valid appointment goes through the same installed trigger and commits.
        member = board.seat_trustee(lo, founder, circle, 1)
        admitted = models.CircleParticipation.objects.select_related("decision").get(
            circle=circle, membership__identity=member)
        positive = admitted.decision
        self.assertEqual((positive.route, positive.constitutional_route, positive.deciding_party),
                         ("GOVERNANCE_CONSTITUTIONAL", "APPOINTMENT", "office:LO_COORDINATOR"))

        service = governance.OrganismGovernanceService(authority=board.authority(), clock=support.database_now)
        for number, rejected_routes in [(2, ("GOVERNANCE_CONSTITUTIONAL", "CIRCLE_FOUNDING")),
                                        (3, ("GOVERNANCE_CONSTITUTIONAL",))]:
            applicant = law.add_member(lo)
            seat = f"trustee-{number}"
            request = service.request_governance_seat(governance.RequestGovernanceSeatCommand(
                **board.command_args(lo, applicant), circle_uuid=circle.circle_uuid,
                seat_rule_uuid=positive.seat_rule_uuid, seat_id=seat))
            pending = models.CircleJoinRequest.objects.get(request_uuid=request.request_uuid)
            for route in rejected_routes:
                with self.subTest(seat=seat, route=route):
                    is_founding = route == "CIRCLE_FOUNDING"
                    decider = applicant if is_founding else founder
                    at = support.database_now()
                    with self.assertRaises(IntegrityError) as refused, transaction.atomic():
                        models.CircleAdmissionDecision.objects.create(
                            request=pending, route=route,
                            constitutional_route="NOT_APPLICABLE" if is_founding else "APPOINTMENT",
                            deciding_party="FOUNDING_BODY" if is_founding else "office:LO_COORDINATOR",
                            deciding_identity=decider, self_decision=is_founding, outcome="ACCEPT",
                            authority_decision_reference=positive.authority_decision_reference,
                            settings_basis=positive.settings_basis.rsplit("#", 1)[0] + "#" + seat,
                            seat_id=seat, seat_rule_uuid=positive.seat_rule_uuid,
                            actor_state="LIVING_ORGANISM", actor_capacity="STANDING",
                            entry_mode="TRANSPORT", enterer_identity=decider,
                            composing_rule_state="NA_TRANSPORT", composer_state="NA_TRANSPORT",
                            occurred_at=at, effective_at=at, received_at=at)
                    self.assertEqual(refused.exception.__cause__.diag.message_primary, "SBG_SEAT_ROUTE_UNAVAILABLE",
                                     "The exact seat route must refuse before any decision or place is stored.")
                    self.assertFalse(models.CircleAdmissionDecision.objects.filter(request=pending).exists())
                    self.assertFalse(models.CircleParticipation.objects.filter(
                        circle=circle, membership=pending.membership).exists())

    def test_term_boundaries_and_adjacent_succession(self):
        """ELECT-09. Exact term boundaries govern a handover, independently of once-only late recording."""
        from src.intevia.services import organism_role_definition_service as definitions
        from src.intevia.services import organism_role_service as roles
        from tests import test_sr_office_actions as office

        self.topic_category_ids = {}
        tick = timedelta(microseconds=1)
        handover = support.database_now() + timedelta(days=1)
        last_end = handover + timedelta(days=1)
        rules = {
            fx.ARTICLE: fx.seat(term={"end": handover.isoformat()}),
            "Successor": fx.seat(term={"start": handover.isoformat(), "end": last_end.isoformat()}),
            "Overlap": fx.seat(term={"start": (handover-tick).isoformat(), "end": last_end.isoformat()}),
        }
        rules["Treasurer"] = {"type": "OFFICE", "place": "TREASURER", "body": "LO_MEMBERSHIP"}
        rules["Secretary"] = {"type": "OFFICE", "place": "SECRETARY", "body": "LO_MEMBERSHIP"}
        secretary_rule = {"type": "OFFICE", "place": "SECRETARY", "route": "APPOINTMENT",
                          "authority": "office:TREASURER", "interim": "Coordinator appoints before voting opens"}
        lo, founder, scope, circle, citation = fx.owner(
            rules=rules, voting=False, vacancies={"Secretary vacancy": secretary_rule})
        incumbent, successor = law.add_member(lo), law.add_member(lo)
        # A recorded Board office independent of the later trustee term.
        helper = office.SROfficeActionTests()
        helper.actor, helper.organism = founder, lo
        service = definitions.OrganismRoleDefinitionService(authority=board.authority(), clock=support.database_now)
        content = definitions.DefinitionContent(code="TREASURER", provisions=(
            definitions.DefinitionProvision("OFFICE_BASIS", "Treasurer", str(citation["version_id"])),))
        version = service.record_role_definition(helper._command(content=content))
        role = models.OrganismRoleDefinition.objects.get(role_uuid=version.role_definition_uuid)
        role_service = roles.OrganismRoleService(authority=board.authority(), clock=support.database_now)
        secretary = definitions.DefinitionContent(
            code="SECRETARY", appointing_authority="NAMED_BODY", appointing_body_reference="office:TREASURER",
            alternative_route_reference="Secretary vacancy#interim",
            provisions=tuple(definitions.DefinitionProvision(kind, reference, str(citation["version_id"]))
                             for kind, reference in (("OFFICE_BASIS", "Secretary"), ("APPOINTING_AUTHORITY", "office:TREASURER"),
                                                     ("ALTERNATIVE_ROUTE", "Secretary vacancy#interim"))))
        secretary_version = service.record_role_definition(helper._command(content=secretary))
        secretary_role = models.OrganismRoleDefinition.objects.get(role_uuid=secretary_version.role_definition_uuid)
        original = fx.office_proposal(lo, founder, successor, secretary_role, self_proposed=False)
        role_service.transition_assignment(helper._transition_command(original, "ASSIGN"))
        pending = fx.office_proposal(lo, founder, founder, secretary_role, self_proposed=True)
        assignment = fx.office_proposal(lo, founder, incumbent, role, self_proposed=False)
        models.EssentialCoverageRequirement.objects.create(
            living_organism=lo, roster_version=1, responsibility_domain_code="TREASURY",
            qualifying_role_code="TREASURER", scope=role.scope, minimum_active_occupants=1,
            effective_from=support.database_now())
        command = helper._transition_command(assignment, "ASSIGN")
        office_end = max(support.database_now(), command.effective_at + tick)
        role_service.transition_assignment(
            replace(command, effective_until_state="FIXED", effective_until=office_end))
        events = models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment)
        source = events.get(action="ASSIGN")
        known = support.database_now()
        self.assertFalse(roles.assignment_in_effect(assignment, command.effective_at-tick, known_at=known))
        self.assertFalse(roles.assignment_in_effect(assignment, command.effective_at, known_at=source.recorded_at-tick))
        self.assertTrue(roles.assignment_in_effect(assignment, office_end-tick, known_at=known))
        self.assertFalse(events.filter(action="END").exists())
        self.assertFalse(roles.assignment_in_effect(assignment, office_end, known_at=known),
                         "ELECT-09 equality: at its exact end the officer has no authority before catch-up.")
        self.assertFalse(roles.assignment_in_effect(assignment, office_end+tick, known_at=known))

        def finish():
            return elections.catch_up_terms(**scope)

        first, second = clarity.CQClarityTests.contended_commands(self, finish, finish)
        self.assertEqual((first, second.result(), finish()), (1, 0, 0))
        ended = events.get(action="END")
        self.assertEqual((ended.effective_at, ended.evidence_reference),
                         (office_end, "elect-expiry:" + str(source.event_uuid)))
        self.assertGreaterEqual(ended.recorded_at, ended.received_at)
        self.assertGreater(ended.received_at, office_end)
        assessment = models.CoverageAssessment.objects.get(case__living_organism=lo,
                                                            evidence_reference="office:" + str(ended.event_uuid))
        self.assertEqual(assessment.effective_at, office_end)
        self.assertEqual(elections.vacancies(**scope, ending_uuid=ended.event_uuid), [])
        fx.enable_voting(scope)

        # Only the named Treasurer can fill this real Secretary vacancy. The same
        # authenticated member can see their own term reason on either side.
        role_service.transition_assignment(helper._transition_command(original, "END", actor=successor))
        ending = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=original, action="END")
        fill = helper._transition_command(pending, "ASSIGN", actor=incumbent)
        member_scope = scope | {"credential": incumbent.credential}
        for moment, message in (
            (command.effective_at-tick, "Your term has not started. Check the start recorded for this place."),
            (fill.effective_at, "Your term has ended. Check the vacancy or succession record for this place."),
        ):
            with self.subTest(holder_cut=moment):
                with self.assertRaises(cq.CQRefused) as refused:
                    elections.appoint_to_vacancy(**member_scope, ending_uuid=ending.event_uuid,
                        command=replace(fill, effective_at=moment))
                self.assertEqual(str(refused.exception), message)
        self.assertFalse(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=pending, action="ASSIGN").exists())

        def elect(provision, person):
            journey = fx.call(self, scope, citation, provision=provision)
            fx.nominate(journey, person)
            ballot = fx.open_election(journey)
            for voter in (founder, incumbent, successor):
                fx.cast(journey, ballot, voter)
            result = cq_reads.vote_status(**journey)["result_uuid"]
            effect = cq_reads.implementation_record(**journey, result_uuid=result)["effect"]
            return models.CircleParticipation.objects.get(participation_uuid=effect["implementation"])

        with self.assertRaises(cq.CQRefused) as unknown:
            fx.call(self, scope, citation, provision="Successor")
        self.assertEqual(str(unknown.exception),
                         "The cited provision gives no confirmed handover date. Supply the provision and evidence of its date.")
        prior = elect(fx.ARTICLE, incumbent)
        with self.assertRaises(cq.CQRefused) as refused:
            fx.call(self, scope, citation, provision="Overlap")
        self.assertEqual(str(refused.exception),
                         "These terms do not meet at the recorded handover. Check the place and both terms before calling the election.")
        following = elect("Successor", successor)
        self.assertEqual(following.effective_at, handover)
        self.assertLess(following.recorded_at, handover)
        trace = elections.authorising_result(**scope, event_uuid=following.participation_uuid)
        self.assertEqual((trace["term_start"], trace["term_end"], trace["preceding_occupancy"]),
                         (handover, last_end, prior.participation_uuid))
        self.assertEqual(models.CircleParticipationEnd.objects.get(participation=prior).effective_at, handover)
        self.assertEqual(models.CircleParticipationEnd.objects.get(participation=following).effective_at, last_end)
        with connection.cursor() as cursor:
            def places(at, seen):
                cursor.execute("""SELECT public.circle_place_at(%s,%s,%s,%s),
                                         public.circle_place_at(%s,%s,%s,%s)""",
                               [circle.pk, prior.membership_id, at, seen,
                                circle.pk, following.membership_id, at, seen])
                return cursor.fetchone()

            seen = support.database_now()
            self.assertEqual(places(handover-tick, seen), (True, False))
            self.assertEqual(places(handover, following.recorded_at-tick), (False, False))
            self.assertEqual(places(handover, seen), (False, True))
            self.assertEqual(places(last_end, seen), (False, False))
        self.assertEqual(finish(), 0)

        from tests.test_cq_decisions import CQDecisionTests

        CQDecisionTests.leave(self, lo, successor)
        lost = elections.authorising_result(**scope, event_uuid=following.participation_uuid)
        self.assertEqual(lost["eligibility_message"],
                         "The successor is no longer eligible. Check the constitutional filling route.")

    def test_recognised_incumbents_require_constitution_consent_and_remaining_term(self):
        """ELECT-10. Recognition of an existing Board has its own authority, consent and effective cut."""
        import json

        data = {}

        def before_birthing(lo, founder):
            scope = {"credential": founder.credential, "organism_uuid": lo.organism_id}
            seat = {"type": "SEAT", "place": "trustee-0", "term": "APPOINTMENT_RECORD"}
            citation = fx.provision(founder, lo, {}, recognitions={
                "Founding": seat | {"authority": "FOUNDING_COORDINATOR"},
                "Members": seat | {"place": "trustee-2", "authority": "body:LO_MEMBERSHIP"},
                "Ordinary": seat | {"authority": "office:LO_COORDINATOR"},
                "Second": seat | {"place": "trustee-1", "authority": "office:LO_COORDINATOR"},
            })
            at = support.database_now()
            claim = {
                "type": "SEAT", "place": "trustee-0", "person": str(founder.identity_id),
                "binding": str(citation["binding_uuid"]), "version": citation["version_id"],
                "provision": "Founding", "original_body": "Annual members meeting",
                "original_date": (at - timedelta(days=12)).date().isoformat(),
                "decision": "Minute 4", "evidence": "AGM signed minute 4",
                "term_start": (at - timedelta(days=12)).isoformat(),
                "term_end": (at + timedelta(days=90)).isoformat(),
            }

            def recognise(args, evidence, consent=None, key=None):
                return elections.recognise_incumbent(**args, claim=evidence, consent_uuid=consent,
                                                     request_uuid=key or uuid4())

            count = models.CircleParticipation.objects.count()
            for args, change, text in (
                (scope, {"evidence": ""}, "The original appointment is unverified. Cite its decision and the evidence required by the constitution."),
                (scope, {"term_end": None}, "The remaining term cannot be established. Cite the term provision, its version and the appointment dates."),
                (scope, {"term_end": at.isoformat()}, "This appointment has ended. Check the vacancy route."),
                (scope, {}, "The holder has not confirmed this place. Ask them to record their consent."),
            ):
                with self.subTest(reason=text), self.assertRaises(cq.CQRefused) as refusal:
                    recognise(args, claim | change)
                self.assertEqual(str(refusal.exception), text)
            self.assertEqual(models.CircleParticipation.objects.count(), count)
            consent = elections.confirm_recognition(**scope, claim=claim, request_uuid=uuid4())["act"]
            key = uuid4()
            done = recognise(scope, claim, consent, key)
            self.assertEqual(recognise(scope, claim, consent, key), done)
            with self.assertRaises(cq.CQRefused) as conflict:
                recognise(scope, claim | {"decision": "Minute 6"}, consent, key)
            self.assertEqual(str(conflict.exception), "This request already records a different act. Check the receipt or use a new request.")
            place = models.CircleParticipation.objects.get(participation_uuid=done["act"])
            trace, = elections.board_continuity(**scope, act_uuid=done["act"])
            self.assertEqual((trace["operation"], trace["request"]["claim"], trace["ilc_event"], trace["enterer"]),
                             ("RECOGNISE", claim, place.participation_uuid, founder.pk))
            self.assertEqual((place.decision.constitutional_route, trace["authority_source"]["route"],
                              trace["request"]["consent"], place.effective_at),
                             ("RECOGNITION", "FOUNDING_COORDINATOR", str(consent), trace["effective_at"]))
            self.assertEqual((trace["holder_consent"], trace["consent_enterer"]), (consent, founder.pk))
            self.assertLessEqual(trace["consented_at"], place.effective_at)
            self.assertGreaterEqual(place.effective_at, at)
            ending = models.CircleParticipationEnd.objects.get(participation=place)
            self.assertEqual(ending.effective_at, at + timedelta(days=90))
            self.assertEqual(ending.composing_rule_reference, "elect-term:recognition:" + str(done["act"]))
            # A forged ending cannot borrow the recognised term's composing rule.
            with self.assertRaises(IntegrityError) as forged, transaction.atomic():
                models.CircleParticipationEnd.objects.create(
                    participation=place, actor_state="NONE", actor_capacity="NA_NO_PARTY", entry_mode="COMPOSED",
                    enterer_identity=founder, composing_rule_state="REFERENCE", composing_rule=ending.composing_rule,
                    composing_rule_reference="elect-term:recognition:" + str(uuid4()), composer_state="REFERENCE",
                    composer_identity=founder, composed_from_event_uuid=uuid4(), occurred_at=ending.occurred_at,
                    effective_at=ending.effective_at, received_at=ending.received_at)
            self.assertEqual(forged.exception.__cause__.diag.message_primary, "ELECT_RECOGNISER")
            self.assertEqual(models.CircleParticipationEnd.objects.filter(participation=place).count(), 1)
            data.update(scope=scope, claim=claim)

        # The sole active founder consents to and recognises their exact AGM
        # seat. The recognised place supplies the real Birthing prerequisite.
        lo, founder = law.ordinary_birth(board_seats=3, elected=(1, 2), seat_founder=False,
                                         before_birthing=before_birthing)
        scope, claim = data["scope"], data["claim"]
        with self.assertRaises(cq.CQRefused) as closed:
            elections.recognise_incumbent(**scope, claim=claim, request_uuid=uuid4())
        self.assertEqual(str(closed.exception), "This recognition requires the authority named in the constitution. Refer it to that body.")
        holder, third = law.add_member(lo), law.add_member(lo)
        holder_scope = scope | {"credential": holder.credential}
        second = claim | {"provision": "Second", "place": "trustee-1", "person": str(holder.identity_id)}
        consent = elections.confirm_recognition(**holder_scope, claim=second, request_uuid=uuid4())["act"]
        with self.assertRaises(cq.CQRefused) as unauthorised:
            elections.recognise_incumbent(**holder_scope, claim=second, consent_uuid=consent, request_uuid=uuid4())
        self.assertEqual(str(unauthorised.exception), "This recognition requires the authority named in the constitution. Refer it to that body.")
        second_act = elections.recognise_incumbent(**scope, claim=second, consent_uuid=consent, request_uuid=uuid4())
        separate, = elections.board_continuity(**holder_scope, act_uuid=second_act["act"])
        self.assertEqual((separate["consent_enterer"], separate["enterer"], separate["authority_source"]["route"]),
                         (holder.pk, founder.pk, "office:LO_COORDINATOR"))
        competing = claim | {"provision": "Ordinary", "person": str(third.identity_id), "decision": "Minute 5"}
        other = elections.confirm_recognition(**(scope | {"credential": third.credential}), claim=competing,
                                              request_uuid=uuid4())["act"]
        count = models.CircleParticipation.objects.count()
        with self.assertRaises(cq.CQRefused) as occupied:
            elections.recognise_incumbent(**scope, claim=competing, consent_uuid=other, request_uuid=uuid4())
        self.assertEqual(str(occupied.exception), "This place is already occupied for that period. Check the incumbency record.")
        self.assertEqual(models.CircleParticipation.objects.count(), count)
        fx.enable_voting(scope)
        claim = claim | {"person": str(third.identity_id), "place": "trustee-2", "provision": "Members"}
        consent = elections.confirm_recognition(**(scope | {"credential": third.credential}), claim=claim, request_uuid=uuid4())["act"]
        journey, _, post = self.topic(scope, markdown=json.dumps({"recognise": claim}))
        cq.put_forward(**journey, version_uuid=post["version"], request_uuid=uuid4())
        ballot = fx.open_election(journey)
        for voter in (founder, holder, third):
            fx.cast(journey, ballot, voter)
        result = cq_reads.vote_status(**journey)["result_uuid"]
        recorded = elections.recognise_incumbent(**scope, claim=claim, consent_uuid=consent, result_uuid=result, request_uuid=uuid4())
        row, = elections.board_continuity(**scope, act_uuid=recorded["act"])
        self.assertEqual((row["authority_source"]["kind"], row["authority_source"]["reference"]), ("CQ_RESULT", str(result)))
        circle = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        circle_commands.activate(**scope, circle_uuid=circle.circle_uuid, request_uuid=uuid4())
        circle.refresh_from_db()
        self.assertEqual(circle.state, "ACTIVE")
        from importlib import import_module
        migration = import_module("core.migrations.0083_elect_board_continuity")
        with self.assertRaises(RuntimeError) as populated, connection.schema_editor(atomic=True) as editor:
            migration.uninstall(None, editor)
        self.assertEqual(str(populated.exception), "ELECT P3 retains recorded recognitions and Board notices.")
        outsider = support.register_test_learner(username="other-" + uuid4().hex)
        with self.assertRaises(disc.DiscussionUnavailable) as unknown:
            elections.board_continuity(**(scope | {"credential": outsider.credential}), act_uuid=recorded["act"])
        self.assertEqual(str(unknown.exception), "This discussion is unavailable to you.")

    def test_holder_notice_seat_dependent_offices_and_vacancies(self):
        """ELECT-11. Hold a notice without a date, then end the actual seat and only dependent offices."""
        from src.intevia.services import library_laws
        from src.intevia.services.organism_role_service import (
            OrganismRoleService,
            assignment_in_effect,
        )
        from tests import test_sr_office_actions as office
        from tests.cq_settings_support import configure
        from tests.test_cq_decisions import CQDecisionTests

        lo, founder = law.ordinary_birth(board_seats=3)
        circle = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        scope = {"credential": founder.credential, "organism_uuid": lo.organism_id}
        rules = {name: {"type": "OFFICE", "place": name.upper(), "body": "LO_MEMBERSHIP"}
                 for name in ("Chair", "Treasurer", "Secretary")}
        recognition = {name: {"type": "OFFICE", "place": name.upper(), "authority": "office:LO_COORDINATOR", "term": "OPEN_ENDED"}
                       for name in rules}
        handover = support.database_now() + timedelta(days=10)
        rules["Deputy"] = {"type": "OFFICE", "place": "VICE_CHAIR", "body": "LO_MEMBERSHIP",
                          "term": {"start": handover.isoformat(), "end": (handover + timedelta(days=10)).isoformat()}}
        recognition["Deputy"] = {"type": "OFFICE", "place": "VICE_CHAIR", "authority": "office:LO_COORDINATOR",
                                "term": "APPOINTMENT_RECORD"}
        citation = fx.provision(founder, lo, rules, recognitions=recognition,
            vacancies={"Chair vacancy": {"type": "OFFICE", "place": "CHAIR", "route": "BY_ELECTION",
                                         "authority": "LO_MEMBERSHIP", "election": "Chair"}})
        holder = board.seat_trustee(lo, founder, circle, 1)
        place = models.CircleParticipation.objects.get(circle=circle, membership__identity=holder)
        own = scope | {"credential": holder.credential}
        roles = OrganismRoleService(authority=board.authority(), clock=support.database_now)
        fixture = office.SROfficeActionTests()
        fixture.actor, fixture.organism = founder, lo
        assignments = {}
        for name, rule in rules.items():
            incumbent = founder if name == "Deputy" else holder
            term_end = handover if name == "Deputy" else None
            role = fx.define_office(lo, founder, rule["place"], "LO_MEMBERSHIP", name, citation,
                                    board_members=name in ("Chair", "Deputy"))
            proposal = fx.office_proposal(lo, founder, incumbent, role, self_proposed=True)
            at = support.database_now()
            claim = {"type": "OFFICE", "place": role.code, "provision": name, "version": citation["version_id"],
                     "binding": str(citation["binding_uuid"]), "original_body": "Trustees", "original_date": at.date().isoformat(),
                     "decision": "Board minute " + name, "evidence": "Signed register", "person": str(incumbent.identity_id),
                     "assignment": str(proposal.assignment_uuid), "term_start": at.isoformat(), "term_end": term_end.isoformat() if term_end else None}
            consent = elections.confirm_recognition(
                **(scope | {"credential": incumbent.credential}), claim=claim, request_uuid=uuid4())["act"]
            act = elections.recognise_incumbent(**scope, claim=claim, consent_uuid=consent, request_uuid=uuid4())["act"]
            event = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=proposal, action="ASSIGN")
            self.assertEqual((event.event_uuid, event.evidence_reference), (act, "elect-recognised:" + str(act)))
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT effective_until_state, effective_until FROM public.core_contextualroleassignmentstateevent "
                    "WHERE event_uuid = %s", [event.event_uuid],
                )
                self.assertEqual(cursor.fetchone(), ("FIXED" if term_end else "NONE", term_end))
            assignments[name] = proposal

        roles.transition_assignment(fixture._transition_command(assignments["Secretary"], "END", actor=holder))
        self.assertFalse(models.CircleParticipationEnd.objects.filter(participation=place).exists())
        # Elect a successor to the founder's fixed Deputy term while the holder still has a Board seat.
        enabled = configure(self, scope, {"clarity_stage": False, "enabled": True})
        third = law.add_member(lo)
        appointment = fx.office_proposal(lo, founder, holder, assignments["Deputy"].role_definition, self_proposed=True)
        journey = fx.call(self, scope, citation, provision="Deputy", place="VICE_CHAIR")
        fx.nominate(journey, holder, assignment=appointment.assignment_uuid)
        ballot = fx.open_election(journey)
        for voter in (founder, holder, third):
            fx.cast(journey, ballot, voter)
        result = cq_reads.vote_status(**journey)["result_uuid"]
        elected = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=appointment, action="ASSIGN")
        self.assertEqual((elected.evidence_reference, elected.effective_at), ("elect-result:" + str(result), handover))
        self.assertLess(elected.recorded_at, handover)
        # The third voter holds neither a seat nor an office. Their departure restores the two-member law route.
        CQDecisionTests.leave(self, lo, third)
        known_before = support.database_now()
        self.assertTrue(assignment_in_effect(appointment, handover, known_at=known_before))
        with self.assertRaises(cq.CQRefused) as wrong:
            elections.resign_board_place(**scope, participation_uuid=place.participation_uuid, request_uuid=uuid4())
        self.assertEqual(str(wrong.exception), "Only the holder can resign this Board place. The holder must give their own notice.")
        request = dict(**own, participation_uuid=place.participation_uuid, request_uuid=uuid4())
        notice = elections.resign_board_place(**request)
        self.assertEqual(elections.resign_board_place(**request), notice)
        self.assertEqual((notice["effect"], notice["message"]), ("AWAITING_BASIS",
            "Your notice is recorded. The effective date needs a constitutional basis. Refer it to the competent body."))
        self.assertFalse(models.CircleParticipationEnd.objects.filter(participation=place).exists())
        # The instrument previously supplied no resignation date. The two eligible
        # members now adopt the exact handover under their existing law-making route.
        import json
        observed, = elections.board_continuity(**own, act_uuid=notice["act"])
        date = observed["effective_at"]
        doc = law.Document(founder)
        version = doc.service.create_successor_version(identity=doc.author, resource_id=doc.name,
            content=json.dumps({"resignations": {"Departure": {"place": "trustee-1", "effect": "DATED",
                               "effective_at": date.isoformat(), "handover": "Give the files to the Secretary"}}}),
            evidence_reference="members:dated-handover")
        doc.transition("publish_library_resource")
        motion = replace(law.adoption(lo, doc, "ARTICLE"), version=version.version_number)
        laws = law.law.OrganismLawService()
        self.assertEqual(laws.adopt_law(law.command(founder, motion)).status, "AGREE")
        self.assertEqual(laws.adopt_law(law.command(holder, motion)).status, "ADOPT")
        completed = dict(**own, participation_uuid=place.participation_uuid, notice_uuid=notice["act"],
                         binding_uuid=motion.proposal, version_id=version.pk,
                         provision="Departure", request_uuid=uuid4())
        end = elections.resign_board_place(**completed)
        self.assertEqual((end["effect"], elections.resign_board_place(**completed)), ("DATED", end))
        ending = models.CircleParticipationEnd.objects.get(participation=place)
        self.assertEqual((ending.effective_at, ending.composed_from_event_uuid), (observed["effective_at"], end["act"]))
        self.assertEqual(elections.catch_up_terms(**scope), 0)
        events = models.ContextualRoleAssignmentStateEvent.objects
        chair_end = events.get(assignment=assignments["Chair"], action="END")
        self.assertEqual(chair_end.effective_at, ending.effective_at)
        self.assertFalse(assignment_in_effect(appointment, handover, known_at=support.database_now()))
        self.assertTrue(assignment_in_effect(appointment, handover, known_at=known_before))
        self.assertFalse(events.filter(assignment=assignments["Treasurer"], action="END").exists())
        assessment = models.CoverageAssessment.objects.get(case__living_organism=lo, evidence_reference="elect-resignation:" + str(end["act"]))
        self.assertEqual(assessment.effective_at, ending.effective_at)
        seat_route, = elections.vacancies(**scope, ending_uuid=place.participation_uuid)
        chair_route, = elections.vacancies(**scope, ending_uuid=chair_end.event_uuid)
        self.assertEqual(seat_route["instruction"]["assessment"], str(assessment.event_uuid))
        self.assertEqual(chair_route["instruction"]["route"], "BY_ELECTION")
        self.assertTrue(any(row["binding"] == citation["binding_uuid"] for row in library_laws.our_laws(**own)["laws"]))
        self.topic(own, title="First discussion after resignation")
        disabled = configure(self, scope, {"enabled": False}, enabled)
        spec = dict(**citation, place="CHAIR", provision="Chair", vacancy_uuid=chair_end.event_uuid)
        wording = elections.attachment(organism_uuid=lo.organism_id, **spec)
        journey, _, post = self.topic(scope, markdown=wording["text"])
        with self.assertRaises(cq.CQRefused) as off:
            elections.call(**journey, **spec, version_uuid=post["version"], request_uuid=uuid4())
        self.assertEqual(str(off.exception), "The vacancy is recorded. Its filling route requires voting to be on before an election can open.")

        # The incumbent remains in office until handover. The cancelled
        # successor leaves that same adjacent term open to a fresh member CQ.
        with self.subTest(source="resignation"):
            self.assertEqual(self._office_deadline(elected, known_before), handover + timedelta(days=10))
            self.assertEqual(self._office_deadline(elected, support.database_now()), handover)
            original = elections.authorising_result(**scope, event_uuid=elected.event_uuid)
            self.assertEqual((original["supporting_place_end_act"], original["supporting_place_end_rule"],
                              original["supporting_place_ends_at"]),
                             (end["act"], "elect-resign:" + str(end["act"]), ending.effective_at))
        configure(self, scope, {"enabled": True}, disabled)
        successor = board.seat_trustee(lo, founder, circle, 2)
        offer = fx.office_proposal(lo, founder, successor, appointment.role_definition, self_proposed=True)
        fresh = self._elect(scope, citation, "Deputy", "VICE_CHAIR", successor,
                            (founder, holder, successor), offer)
        new_award = events.get(assignment=offer, action="ASSIGN")
        self.assertEqual((new_award.effective_at, new_award.evidence_reference),
                         (handover, "elect-result:" + str(fresh)))
        self.assertNotEqual(fresh, result)
        self.assertTrue(assignment_in_effect(offer, handover, known_at=support.database_now()))

    @staticmethod
    def _office_deadline(award, known):
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.elect_office_deadline(%s,%s)", [award.pk, known])
            return cursor.fetchone()[0]

    def _elect(self, scope, citation, provision, place, nominee, voters, offer=None):
        journey = fx.call(self, scope, citation, provision=provision, place=place)
        fx.nominate(journey, nominee, assignment=offer.assignment_uuid if offer else None)
        ballot = fx.open_election(journey)
        for voter in voters:
            fx.cast(journey, ballot, voter)
        return cq_reads.vote_status(**journey)["result_uuid"]

    def test_future_office_tracks_original_seat_term_and_lo_departure(self):
        """ELECT-12. The original Board place limits the award across knowledge cuts."""
        from src.intevia.services.organism_role_service import assignment_in_effect
        from tests.test_cq_decisions import CQDecisionTests

        lo, founder = law.ordinary_birth(board_seats=2, elected=(1,))
        scope = {"credential": founder.credential, "organism_uuid": lo.organism_id}
        handover = support.database_now() + timedelta(days=10)
        seat_end, office_end = handover + timedelta(days=2), handover + timedelta(days=10)
        rules = {fx.ARTICLE: fx.seat(term={"end": seat_end.isoformat()}),
                 "Deputy": {"type": "OFFICE", "place": "VICE_CHAIR", "body": "LO_MEMBERSHIP",
                            "term": {"start": handover.isoformat(), "end": office_end.isoformat()}}}
        recognition = {"Deputy": {"type": "OFFICE", "place": "VICE_CHAIR",
                                  "authority": "office:LO_COORDINATOR", "term": "APPOINTMENT_RECORD"}}
        citation = fx.provision(founder, lo, rules, recognitions=recognition)
        role = fx.define_office(lo, founder, "VICE_CHAIR", "LO_MEMBERSHIP", "Deputy", citation, board_members=True)
        incumbent = fx.office_proposal(lo, founder, founder, role, self_proposed=True)
        at = support.database_now()
        claim = {"type": "OFFICE", "place": role.code, "provision": "Deputy", "version": citation["version_id"],
                 "binding": str(citation["binding_uuid"]), "original_body": "Trustees", "original_date": at.date().isoformat(),
                 "decision": "Board minute", "evidence": "Signed register", "person": str(founder.identity_id),
                 "assignment": str(incumbent.assignment_uuid), "term_start": at.isoformat(), "term_end": handover.isoformat()}
        consent = elections.confirm_recognition(**scope, claim=claim, request_uuid=uuid4())["act"]
        elections.recognise_incumbent(**scope, claim=claim, consent_uuid=consent, request_uuid=uuid4())
        fx.enable_voting(scope)
        holder, third = law.add_member(lo), law.add_member(lo)
        voters = (founder, holder, third)
        seat_vote = self._elect(scope, citation, fx.ARTICLE, "trustee-1", holder, voters)
        seat = models.CircleParticipation.objects.get(circle__parent_organism=lo, membership__identity=holder)
        offer = fx.office_proposal(lo, founder, holder, role, self_proposed=True)
        self._elect(scope, citation, "Deputy", "VICE_CHAIR", holder, voters, offer)
        award = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=offer, action="ASSIGN")
        known = support.database_now()
        with self.subTest(source="elect-term"):
            self.assertTrue(assignment_in_effect(offer, handover, known_at=known))
            self.assertFalse(assignment_in_effect(offer, seat_end, known_at=known))
            self.assertEqual(self._office_deadline(award, known), seat_end)
            info = elections.authorising_result(**scope, event_uuid=award.event_uuid)
            self.assertEqual((info.get("supporting_place_end_rule"), info.get("supporting_place_end_act"),
                              info.get("supporting_place_ends_at")),
                             ("elect-term:" + str(seat_vote), seat.participation_uuid, seat_end))
        CQDecisionTests.leave(self, lo, holder)
        transition = models.OrganismMembershipTransition.objects.get(membership=seat.membership, resulting_state="ENDED")
        self.assertLess(transition.effective_at, handover)
        self.assertEqual(self._office_deadline(award, support.database_now()), handover)
        info = elections.authorising_result(**scope, event_uuid=award.event_uuid)
        self.assertEqual((info["supporting_place_end_act"], info["supporting_place_ends_at"]),
                         (transition.event_uuid, transition.effective_at))
        self.assertEqual(self._office_deadline(award, known), seat_end)

    def test_advance_cites_dated_resignation_assessment_before_term_catch_up(self):
        """ELECT-13. First vacancy writer binds the exact assessment across a future date."""
        lo, founder = law.ordinary_birth(board_seats=2)
        scope = {"credential": founder.credential, "organism_uuid": lo.organism_id}
        seat = models.CircleParticipation.objects.get(circle__parent_organism=lo, membership__identity=founder)
        fx.enable_voting(scope)
        at = support.database_now()
        # Pick the nearest real date still ahead after the instrument is adopted.
        # These are fixture dates in a single Article, never product intervals.
        dates = {str(seconds): at + timedelta(seconds=seconds) for seconds in (3, 10, 60)}
        dates["Past"] = seat.effective_at - timedelta(seconds=1)
        clauses = {key: {"place": seat.seat_id, "effect": "DATED", "effective_at": value.isoformat()}
                   for key, value in dates.items()}
        citation = fx.provision(founder, lo, {}, resignations=clauses)
        request = scope | citation | {"participation_uuid": seat.participation_uuid}
        assessments = models.CoverageAssessment.objects.filter(case__living_organism=lo)
        with self.subTest(refusal="date outside seat term"):
            with self.assertRaises(cq.CQRefused) as invalid:
                elections.resign_board_place(**request, provision="Past", request_uuid=uuid4())
            self.assertEqual(str(invalid.exception), "This resignation date cannot be used. It must fall within your current term "
                             "and not before your notice. Check the date the constitution gives.")
        self.assertFalse(models.CircleParticipationEnd.objects.filter(participation=seat).exists())
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS elect_vacancy_from_seat IMMEDIATE")
            reserve = support.database_now() + timedelta(seconds=1)
            clause, due = next((key, value) for key, value in dates.items() if value > reserve)
            end = elections.resign_board_place(**request, provision=clause, request_uuid=uuid4())
        recorded_end = models.CircleParticipationEnd.objects.get(participation=seat)
        self.assertEqual((recorded_end.effective_at, recorded_end.composed_from_event_uuid), (due, end["act"]))
        self.assertLess(recorded_end.received_at, due)
        reference = "elect-resignation:" + str(end["act"])
        self.assertFalse(assessments.filter(evidence_reference=reference).exists())
        self.assertEqual(elections.vacancies(**scope, ending_uuid=seat.participation_uuid), [])
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep_until(%s)", [due])
        cq.catch_up(credential=founder.credential)
        vacancy, = elections.vacancies(**scope, ending_uuid=seat.participation_uuid)
        self.assertIsNotNone(vacancy["instruction"]["assessment"], "First seat vacancy must cite the resignation coverage")
        assessment = assessments.get(evidence_reference=reference)
        self.assertEqual((vacancy["instruction"]["assessment"], assessment.effective_at), (str(assessment.event_uuid), due))
        cq.catch_up(credential=founder.credential)
        self.assertEqual(elections.catch_up_terms(**scope), 0)
        self.assertEqual(elections.vacancies(**scope, ending_uuid=seat.participation_uuid), [vacancy])
        self.assertEqual(assessments.filter(evidence_reference=reference).count(), 1)
