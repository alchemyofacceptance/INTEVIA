"""C1-102. A committee exercises only the authority its adopted law gives it."""

from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from django.db import connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import (
    circle_commands as circles,
    discussion as disc,
    committees,
    cq,
    cq_reads,
    elections,
)
from src.intevia.services.organism_entitlement_service import entitlement_effective
from tests import (
    committee_support as c,
    election_support as fx,
    s015_0021_support as support,
)
from tests import test_election_mandates as mandates, test_law_acts as law


class CommitteeTests(TransactionTestCase):
    topic = mandates.ElectionMandateTests.topic
    decision = mandates.ElectionMandateTests.decision
    activate_board = mandates.ElectionMandateTests.activate_board
    constitution = mandates.ElectionMandateTests.constitution
    committee = mandates.ElectionMandateTests.committee
    hand_over_committee_office = (
        mandates.ElectionMandateTests.hand_over_committee_office
    )

    def board(self, *, subscription=False, extra=False):
        self.topic_category_ids = {}
        lo, founder = law.ordinary_birth(
            offering_choice="COMPLIMENTARY_BETA" if subscription else "FREE",
            board_seats=4 if extra else 3,
            elected=(3,) if extra else (),
        )
        return self.activate_board(lo, founder)

    def rule(self, lo, board, voters, required):
        doc, version = c.published(
            voters[0], {"committees": {"chair_board_member": required}}
        )
        return self.decision(board, c.instrument(lo, doc, version), voters)

    def refusal(self, scope, att, code):
        with self.assertRaises(cq.CQRefused) as error:
            c.start(self, scope, att)
        self.assertEqual(
            (error.exception.code, str(error.exception)), (code, cq.MESSAGES[code])
        )

    def test_paired_designation_on_the_free_route(self):
        """C-01. Free formation records the exact Policy, terms and level-one finding together."""
        lo, general, board, voters = c.formation_board(self, offering="FREE")
        founder = voters[0]
        circle = self.committee(general)
        local = general | {"circle_uuid": circle.circle_uuid}
        for voter in voters[1:]:
            c.join(general, circle, voter)
        forming, spec = c.mandate(
            lo, founder, circle, "1", kind="AD_HOC", task="review reserves",
            completion_body="GOVERNANCE", terms={"committee_work": {"return_to": "CHAIR"}},
            policy_fields={"committee_work": {"return_to": "CHAIR"}}, publisher=c.first_version,
        )
        self.assertEqual(committees.history(**local), [])

        # Use a missing successor of the actual terms. Refusal must leave neither binding.
        unavailable = spec["terms"] | {"version": spec["terms"]["version"] + 1}
        doc, version = c.first_version(
            founder, {"committee": spec | {"terms": unavailable}}
        )
        incomplete = c.instrument(lo, doc, version)
        self.refusal(board, incomplete, "CMT_FORM")
        failed_bindings = disc._rows(
            "SELECT binding_uuid FROM public.core_organismlawbinding WHERE binding_uuid IN (%s,%s)",
            [incomplete["spec"]["proposal"], spec["terms"]["binding"]],
        )
        self.assertEqual(failed_bindings, [])

        self.assertFalse(entitlement_effective(lo.organism_id, "COMMITTEE_CHAIR_RULE"))
        override, _ = c.mandate(
            lo,
            founder,
            circle,
            "1",
            terms={"chair_board_member": True},
            publisher=c.first_version,
        )
        self.refusal(board, override, "CMT_TIER")

        receipt = c.decide_instrument(self, board, forming, voters)
        proof = receipt["plan"]["committee"]
        finding = proof["officer_finding"]
        self.assertEqual(
            (finding["count"], finding["required"], finding["offices"]), (0, 3, [])
        )
        self.assertTrue(proof["caution"])
        bindings = disc._rows(
            """SELECT b.law_class, e.event_uuid FROM public.core_organismlawbinding b
               JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
               WHERE b.binding_uuid IN (%s,%s) ORDER BY b.law_class""",
            [forming["spec"]["proposal"], spec["terms"]["binding"]],
        )
        self.assertEqual(
            bindings,
            [
                {"law_class": "POLICY", "event_uuid": receipt["implementation"]},
                {"law_class": "TERMS_OF_REFERENCE", "event_uuid": receipt["result"]},
            ],
        )
        self.assertEqual(len(committees.history(**local)), 1)

        # One real activity follows the mandate from a live committee to a Board decision.
        binding = forming["spec"]["proposal"]
        activity_id = c.assigned_activity(founder, circle, binding)
        bound = c.decide_instrument(
            self, local, c.step(local, binding, "BIND_WORK", activity=str(activity_id)), voters
        )
        activity = models.ServiceActivity.objects.get(activity_id=activity_id)
        original = models.ServiceActivityAssignment.objects.get(activity=activity)
        self.assertEqual(committees.work_holder(activity, original, "default"), founder.pk)
        c.decide_instrument(
            self, board, c.step(board, binding, "COMPLETE_TASK", task="review reserves"), voters
        )
        (held,) = disc._rows(
            "SELECT receipt,office,holder FROM public.core_cmtcustody WHERE predecessor=%s",
            [bound["implementation"]],
        )
        self.assertEqual((held["office"], held["holder"]), (None, None))
        receiving = models.ContextualRoleAssignment.objects.get(
            circle__circle_uuid=board["circle_uuid"],
            membership__identity=voters[1],
            role_definition__code="COORDINATOR",
        )
        recovery = c.step(
            board, binding, "REASSIGN_WORK", activity=str(activity_id),
            predecessor=str(held["receipt"]), office=str(receiving.assignment_uuid),
        )
        self.refusal(local, recovery, "CMT_ENDED")
        recovered = c.decide_instrument(self, board, recovery, voters)
        (custody,) = disc._rows(
            "SELECT receipt,office,holder FROM public.core_cmtcustody WHERE predecessor=%s", [held["receipt"]]
        )
        self.assertEqual(
            custody, {"receipt": recovered["implementation"], "office": receiving.pk, "holder": voters[1].pk}
        )
        self.assertEqual(committees.work_holder(activity, original, "default"), voters[1].pk)

    def test_level_ceiling_exact_recommendation_and_parent_scope(self):
        """C-02. A level-two parent needs the Board for Policy but can form an authorised child."""
        lo, general, board, voters = self.board(subscription=True)
        founder = voters[0]
        parent, child = self.committee(general), self.committee(general)
        local = general | {"circle_uuid": parent.circle_uuid}
        inside = general | {"circle_uuid": child.circle_uuid}
        for person in voters[1:]:
            c.join(general, parent, person)
        forming, _ = c.mandate(
            lo,
            founder,
            parent,
            "2",
            areas={"reserves": {"POLICY": "ADOPT", "PROCEDURE": "ADOPT"}},
            terms={"form_children": True},
            policy_fields={"committees": {"chair_board_member": False, "sub_board_member": True}},
            publisher=c.first_version,
        )
        c.decide_instrument(self, board, forming, voters)
        binding = forming["spec"]["proposal"]

        doc, version = c.first_version(
            founder,
            {
                "committee_adoption": {"mandate": binding, "area": "reserves"},
                "text": "Hold reserves.",
            },
        )
        proposal = c.instrument(lo, doc, version)
        self.refusal(local, proposal, "CMT_LEVEL_2")
        self.refusal(local, c.instrument(lo, doc, version, "PROCEDURE"), "CMT_LEVEL_2")
        self.assertEqual(
            disc._rows(
                "SELECT binding_uuid FROM public.core_organismlawbinding WHERE version_id=%s",
                [version.pk],
            ),
            [],
        )
        exact = dict(
            area="reserves",
            version=version.pk,
            fingerprint=sha256(version.content.encode()).hexdigest(),
        )
        decision = c.decide_instrument(
            self, local, c.step(local, binding, "RECOMMEND", **exact), voters
        )
        proposal["spec"]["recommendation"] = str(decision["result"])
        adopted = c.decide_instrument(self, board, proposal, voters)
        self.assertEqual(adopted["plan"]["recommendation"], str(decision["result"]))
        exact_adoption = disc._rows(
            """SELECT b.version_id, e.event_uuid FROM public.core_organismlawbinding b
               JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
               WHERE b.binding_uuid=%s""",
            [proposal["spec"]["proposal"]],
        )
        self.assertEqual(
            exact_adoption,
            [{"version_id": version.pk, "event_uuid": adopted["implementation"]}],
        )

        self.assertTrue(
            entitlement_effective(lo.organism_id, "COMMITTEE_PARENT_FORMATION")
        )
        sub, _ = c.mandate(
            lo,
            founder,
            child,
            "3",
            kind="SUB_COMMITTEE",
            parent=str(parent.circle_uuid),
            terms={"chair_board_member": False},
            publisher=c.first_version,
        )
        child_receipt = c.decide_instrument(self, local, sub, voters)
        self.assertEqual(
            child_receipt["plan"]["committee"]["delegating_parent"], binding
        )
        # Initiation checks the attachment before any ballot opens. One real chair can try.
        exceeds = c.step(inside, sub["spec"]["proposal"], "RECOMMEND", **exact)
        self.refusal(inside, exceeds, "CMT_LEVEL_3")

        # Local terms govern the chair. The LO's Policy independently requires a trustee.
        self.assertEqual(child_receipt["plan"]["committee"]["chair_basis"]["key"], "terms.chair_board_member")
        # Keep one ordinary member in the child when its only trustee leaves.
        remaining = c.join(general, child, law.add_member(lo))
        place = models.CircleParticipation.objects.get(circle=child, membership__identity=founder)
        trustees = disc._rows(
            """SELECT member_id FROM public.cmt_trustees(
                   %s,%s,statement_timestamp(),statement_timestamp())""",
            [lo.pk, child.pk],
        )
        self.assertEqual(trustees, [{"member_id": place.membership_id}])

        departure = circles.leave(**inside, participation_uuid=place.participation_uuid, request_uuid=uuid4(), confirmed=True)
        # Fix effective time at the departure. Read after commit, when its end is visible.
        (state,) = disc._rows(
            """WITH cut AS (SELECT %s::timestamptz AS eff, statement_timestamp() AS known)
               SELECT public.cmt_live(%s,cut.eff,cut.known) AS mandate_live,
                      public.circle_place_at(%s,%s,cut.eff,cut.known) AS remaining_place,
                      (SELECT count(*) FROM public.cmt_trustees(%s,%s,cut.eff,cut.known)) AS trustees
                 FROM cut""",
            [departure.effective_at, sub["spec"]["proposal"], child.pk,
             remaining.membership_id, lo.pk, child.pk],
        )
        self.assertEqual(state, {"mandate_live": True, "remaining_place": True, "trustees": 0})
        gaps = disc._rows(
            """SELECT effective_at,basis->>'binding' AS binding FROM public.core_cmtcomposition
               WHERE mandate=%s AND requirement='TRUSTEE' AND unmet""", [sub["spec"]["proposal"]],
        )
        self.assertEqual(gaps, [{"effective_at": departure.effective_at, "binding": binding}])

    def test_chair_and_floor_follow_the_constitution_at_the_seat_boundary(self):
        """C-03. The constitutional Board-place rule bounds the actual committee office."""
        from tests import (
            cq_authority_support as support_board,
            test_sr_office_actions as offices,
        )
        from src.intevia.services.organism_role_service import OrganismRoleService
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
        )

        # ELECT-10's confirmed incumbent route gives the founder a genuine fixed term.
        # The same person can then chair the generic Circle they form.
        def recognise_founder(lo, founder):
            scope = {"organism_uuid": lo.organism_id, "credential": founder.credential}
            citation = fx.provision(
                founder,
                lo,
                {},
                recognitions={
                    "Founding": {
                        "authority": "FOUNDING_COORDINATOR",
                        "type": "SEAT",
                        "place": "trustee-0",
                        "term": "APPOINTMENT_RECORD",
                    },
                },
                resignations={"Leave": {"place": "trustee-0", "effect": "ON_NOTICE"}},
            )
            at = support.database_now()
            claim = {
                "type": "SEAT",
                "place": "trustee-0",
                "person": str(founder.identity_id),
                "binding": str(citation["binding_uuid"]),
                "version": citation["version_id"],
                "provision": "Founding",
                "original_body": "Annual members meeting",
                "original_date": (at - timedelta(days=1)).date().isoformat(),
                "decision": "Minute 4",
                "evidence": "Signed trustee appointment",
                "term_start": (at - timedelta(days=1)).isoformat(),
                "term_end": (at + timedelta(days=1)).isoformat(),
            }
            consent = elections.confirm_recognition(
                **scope, claim=claim, request_uuid=uuid4()
            )
            elections.recognise_incumbent(
                **scope, claim=claim, consent_uuid=consent["act"], request_uuid=uuid4()
            )
            founding_provision.update(citation)

        def choose_at_activation(request):
            with self.assertRaisesMessage(circles.CircleAccessRefused,
                    "Choose whether committee chairs must be Board members before activating GOVERNANCE."):
                circles.activate(**request)
            self.assertFalse(models.CircleStateEvent.objects.filter(event_uuid=request["request_uuid"]).exists())
            self.assertEqual(circles.activate(**request, committee_chair_board_member=True), request["request_uuid"])
            self.assertEqual(circles.activate(**request, committee_chair_board_member=True), request["request_uuid"])
            with self.assertRaises(circles.CircleAccessRefused):
                circles.activate(**request, committee_chair_board_member=False)
            self.activation_event = request["request_uuid"]

        founding_provision = {}
        lo, general, board, trustees = c.formation_board(
            self,
            offering="COMPLIMENTARY_BETA",
            seats=4,
            recognise_founder=recognise_founder,
            activation_probe=choose_at_activation,
        )
        founder = trustees[0]
        gov = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        original = models.CircleParticipation.objects.get(
            circle=gov, membership__identity=founder
        )
        end = models.CircleParticipationEnd.objects.get(
            participation=original
        ).effective_at
        committee = self.committee(general)
        local = general | {"circle_uuid": committee.circle_uuid}
        c.join(general, committee, trustees[2])
        formed, mandate = c.mandate(
            lo,
            founder,
            committee,
            "2",
            terms={"chair_board_member": False},
            publisher=c.first_version,
        )
        (initial,) = disc._rows(
            "SELECT public.cmt_chair_rule(%s,'{}',statement_timestamp(),statement_timestamp()) AS rule", [lo.pk]
        )
        self.assertEqual(initial["rule"], {"activation": str(self.activation_event), "source":"ACTIVATE",
                                          "required":True, "key":"activation.chair_board_member"})

        # Board fallback and terms can be distinguished before the Article governs.
        doc, v = c.first_version(founder, {"committees": {"chair_board_member": False}})
        policy = c.instrument(lo, doc, v)
        c.decide_instrument(self, board, policy, trustees)
        rules = disc._rows(
            """SELECT public.cmt_chair_rule(%s,'{}',statement_timestamp(),statement_timestamp()) AS fallback,
            public.cmt_chair_rule(%s,'{"chair_board_member":true}',statement_timestamp(),statement_timestamp()) AS terms""",
            [lo.pk, lo.pk],
        )[0]
        self.assertEqual(
            (rules["fallback"]["binding"], rules["fallback"]["required"]),
            (policy["spec"]["proposal"], False),
        )
        self.assertEqual(
            (rules["terms"]["key"], rules["terms"]["required"]),
            ("terms.chair_board_member", True),
        )
        charter, v = c.first_version(
            founder, {"committees": {"chair_board_member": True}}
        )
        article = c.instrument(lo, charter, v, "ARTICLE")
        c.decide_instrument(self, general, article, trustees)
        receipt = c.decide_instrument(self, board, formed, trustees)
        effect = receipt["plan"]["committee"]
        self.assertEqual(
            (effect["chair_basis"]["binding"], effect["chair_basis"]["required"]),
            (article["spec"]["proposal"], True),
        )
        chair_id = effect["chair_id"]
        chair = models.ContextualRoleAssignment.objects.get(pk=chair_id)
        self.assertEqual(
            (str(chair.assignment_uuid), chair.circle_id, chair.role_definition.code),
            (mandate["chair"], committee.pk, "COORDINATOR"),
        )
        now = support.database_now()
        with connection.cursor() as cur:
            cur.execute(
                """SELECT public.cmt_chair_end(%s,%s),
                public.disc_office(%s,%s,%s,%s) IS NULL,
                public.cmt_empty(%s,%s,%s), public.cmt_empty(%s,%s,%s),
                public.cmt_gap_date(%s,'CHAIR',%s,%s),
                public.cmt_custody_due(%s,%s,%s,%s,%s)""",
                [
                    chair_id,
                    now,
                    lo.pk,
                    committee.pk,
                    founder.pk,
                    end,
                    committee.pk,
                    end - timedelta(microseconds=1),
                    now,
                    committee.pk,
                    end,
                    now,
                    formed["spec"]["proposal"],
                    end + timedelta(microseconds=1),
                    now,
                    chair_id,
                    formed["spec"]["proposal"],
                    now,
                    end + timedelta(microseconds=1),
                    now,
                ],
            )
            self.assertEqual(cur.fetchone(), (end, True, False, True, end, end))
        endings = models.ContextualRoleAssignmentStateEvent.objects.filter(
            assignment=chair, action="END"
        )
        self.assertFalse(endings.exists())

        # At least three eligible Board voters must remain to authorise the replacement.
        elections.resign_board_place(
            **general,
            participation_uuid=original.participation_uuid,
            **founding_provision,
            provision="Leave",
            request_uuid=uuid4(),
        )
        earlier_end = models.CircleParticipationEnd.objects.get(
            participation=original, composing_rule_reference__startswith="elect-resign:"
        ).effective_at
        with self.assertRaisesMessage(
            circles.CircleAccessRefused, cq.MESSAGES["CMT_CHAIR_GAP"]
        ):
            circles.recover_circle(**local, request_uuid=uuid4())
        elections.catch_up_terms(**general)
        elections.catch_up_terms(**general)
        self.assertEqual(endings.get().effective_at, earlier_end)
        self.assertEqual(
            [
                g["requirement"]
                for g in committees.history(**local)[0]["composition"]
                if g["unmet"]
            ],
            ["CHAIR"],
        )
        (gap,) = disc._rows(
            """SELECT effective_at,recorded_at FROM public.core_cmtcomposition
            WHERE mandate=%s AND requirement='CHAIR' AND unmet""",
            [formed["spec"]["proposal"]],
        )
        self.assertEqual(gap["effective_at"], earlier_end)
        self.assertGreaterEqual(gap["recorded_at"], earlier_end)

        future = uuid4()
        c.decide_instrument(
            self,
            board,
            c.step(
                board,
                formed["spec"]["proposal"],
                "FILL_CHAIR",
                person=str(trustees[2].identity_id),
                assignment=str(future),
            ),
            trustees[1:],
        )
        office = OrganismRoleService(
            authority=support_board.authority(), clock=support.database_now
        )
        place = models.CircleParticipation.objects.get(
            circle=committee, membership__identity=trustees[2]
        )
        entered = general | {"circle_uuid": committee.circle_uuid}
        office.nominate_circle_coordinator(
            **entered,
            participation_uuid=place.participation_uuid,
            assignment_uuid=future,
            request_uuid=uuid4(),
        )
        nominated = models.ContextualRoleAssignment.objects.get(assignment_uuid=future)
        OfficeAppointmentConsentService(
            authority=support_board.authority(), clock=support.database_now
        ).record_office_consent(
            offices.SROfficeActionTests()._consent_command(place.membership, nominated)
        )
        office.appoint_circle_coordinator(
            **entered, assignment_uuid=future, request_uuid=uuid4()
        )
        self.assertFalse(committees.history(**local)[0]["composition"][-1]["unmet"])

    def test_end_boundary_and_stale_decision_preserve_history(self):
        """C-04. A dated mandate permits investigation until its exclusive end."""
        import json

        lo, general, board, voters = self.board()
        founder = voters[0]

        # A stored deadline can be checked on both sides without waiting for the clock.
        dated = self.committee(general)
        dated_scope = general | {"circle_uuid": dated.circle_uuid}
        until = support.database_now() + timedelta(days=1)
        instrument, _ = c.mandate(
            lo,
            founder,
            dated,
            "4",
            kind="AD_HOC",
            ends_at=until.isoformat(),
            publisher=c.first_version,
        )
        c.decide_instrument(self, board, instrument, voters)
        dated_binding = instrument["spec"]["proposal"]
        finding = c.step(
            dated_scope,
            dated_binding,
            "INVESTIGATE",
            area="reserves",
            finding="Invoices received.",
        )
        self.refusal(
            dated_scope,
            c.step(
                dated_scope,
                dated_binding,
                "REPORT",
                area="reserves",
                finding="Invoices received.",
            ),
            "CMT_LEVEL_4",
        )
        cuts = disc._rows(
            """SELECT public.cmt_live(%s,cut,statement_timestamp()) AS live,
                      public.cmt_work_plan(b.organism_id,b.board_uuid,%s::jsonb,cut) AS plan
                 FROM public.core_discboard b
                 CROSS JOIN (VALUES (%s::timestamptz),(%s::timestamptz)) t(cut)
                WHERE b.circle_id=%s AND b.organism_id=%s ORDER BY cut""",
            [
                dated_binding,
                json.dumps(finding),
                until - timedelta(microseconds=1),
                until,
                dated.pk,
                lo.pk,
            ],
        )
        before, at_end = cuts
        self.assertEqual(
            (before["live"], before["plan"]["committee"]["finding"]),
            (True, "Invoices received."),
        )
        self.assertEqual((at_end["live"], at_end["plan"]["code"]), (False, "CMT_ENDED"))

    def test_reserves_work_returns_once_and_stays_with_the_board_without_a_chair(self):
        """C-05. Seat-linked work returns once to its chair, then is held for the Board (interim C1-113)."""
        from dataclasses import asdict
        from src.intevia.services import service_activity_service as activities
        from src.intevia.services.service_service import GovernedService
        from src.intevia.services.service_authority import (
            ServiceCommandAuthority,
            ServiceCommandAuthorityResponse,
        )
        from tests.test_law_records import LibraryFixtureCapability

        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
        )
        from src.intevia.services.organism_role_service import OrganismRoleService
        from tests import cq_authority_support, test_sr_office_actions

        def define_chair_before_voting(owner, issuer, binding, version_id):
            self.chair_article = {"binding": binding, "version": version_id}
            self.chair_role = fx.define_office(owner, issuer, "CHAIR", "LO_MEMBERSHIP", "Board chair",
                                               {"version_id": version_id}, board_members=True)

        lo, general, board, people = c.formation_board(
            self, offering="COMPLIMENTARY_BETA",
            after_article=define_chair_before_voting,
            charter_fields={
                "resignations": {"Trustee notice": {"place": "trustee-2", "effect": "ON_NOTICE"}},
                "recognitions": {"Board chair": {"type": "OFFICE", "place": "CHAIR",
                                                "authority": "office:LO_COORDINATOR", "term": "OPEN_ENDED"}},
            },
        )
        founder, worker, other = people
        circle = self.committee(general)
        local = general | {"circle_uuid": circle.circle_uuid}
        worker_place = c.join(general, circle, worker)
        c.join(general, circle, other)

        # Seat the second officeholder on the ordinary route before designation.
        authority = cq_authority_support.authority()
        roles = OrganismRoleService(authority=authority, clock=support.database_now)
        office_uuid = uuid4()
        roles.nominate_circle_coordinator(
            **local,
            participation_uuid=worker_place.participation_uuid,
            assignment_uuid=office_uuid,
            request_uuid=uuid4(),
        )
        office = models.ContextualRoleAssignment.objects.get(
            assignment_uuid=office_uuid
        )
        consent = test_sr_office_actions.SROfficeActionTests()._consent_command(
            worker_place.membership, office
        )
        OfficeAppointmentConsentService(
            authority=authority, clock=support.database_now
        ).record_office_consent(consent)
        roles.appoint_circle_coordinator(
            **local, assignment_uuid=office_uuid, request_uuid=uuid4()
        )

        forming, spec = c.mandate(
            lo,
            founder,
            circle,
            "1",
            terms={"committee_work": {"return_to": "CHAIR"}},
            policy_fields={"committee_work": {"return_to": "CHAIR"}},
            publisher=c.first_version,
        )
        formed = c.decide_instrument(self, board, forming, people)
        mandate = forming["spec"]["proposal"]
        chair_id = formed["plan"]["committee"]["chair_id"]

        # The exact in-scope adoption is also the basis of the one reserves CSA.
        doc, version = c.first_version(
            founder,
            {
                "committee_adoption": {"mandate": mandate, "area": "reserves"},
                "text": "Review reserves.",
            },
        )
        proposal = c.instrument(lo, doc, version)
        policy = c.decide_instrument(self, local, proposal, people)
        (adopted,) = disc._rows(
            """SELECT b.binding_uuid, b.version_id, e.event_uuid
                 FROM public.core_organismlawbinding b
                 JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
                WHERE b.binding_uuid=%s""",
            [proposal["spec"]["proposal"]],
        )
        self.assertEqual(
            (
                str(adopted["binding_uuid"]),
                adopted["version_id"],
                adopted["event_uuid"],
            ),
            (proposal["spec"]["proposal"], version.pk, policy["implementation"]),
        )

        class ActivityGrant:
            def authorise(self, *, request):
                return ServiceCommandAuthorityResponse(
                    **asdict(request), authority_reference="fixture:reserve-work"
                )

        source = GovernedService(
            authority=law.ContributionAuthority(LibraryFixtureCapability())
        )
        catalog = source.create_service(
            identity=founder.credential,
            service_id="reserves-" + uuid4().hex,
            capability_purpose="Review reserves",
            domain_intent="Service",
            evidence_reference="fixture:reserves",
        )
        source.transition_service(
            identity=founder.credential,
            service_id=catalog.service_id,
            command="publish_service",
            evidence_reference="fixture:publish",
        )
        service = activities.ServiceActivityService(
            authority=ServiceCommandAuthority(provider=ActivityGrant()),
            clock=support.database_now,
        )
        activity_id = uuid4()

        def command(actor=founder):
            return dict(
                credential=actor.credential,
                request_reference=uuid4().hex,
                idempotency_key=uuid4().hex,
                occurred_at=support.database_now(),
                activity_id=activity_id,
            )

        service.create_service_activity(
            activities.CreateServiceActivityCommand(
                **command(),
                service_version_pk=catalog.current_version_id,
                initiating_domain=activities.InitiatingDomain.SERVICE,
                initiating_domain_reference="committee:" + str(circle.circle_uuid),
                activity_basis_reference=str(adopted["binding_uuid"]),
            )
        )
        service.assign_service_activity(
            activities.AssignServiceActivityCommand(
                **command(),
                assignee_identity_id=founder.identity_id,
                assignment_reference="reserve-review",
                assignment_basis_reference="fixture:committee-chair",
            )
        )
        bound = c.decide_instrument(
            self,
            local,
            c.step(local, mandate, "BIND_WORK", activity=str(activity_id)),
            people,
        )
        activity = models.ServiceActivity.objects.get(activity_id=activity_id)
        initial = models.ServiceActivityAssignment.objects.get(activity=activity)
        self.assertEqual(
            committees.work_holder(activity, initial, "default"), founder.pk
        )
        moved = c.decide_instrument(
            self,
            local,
            c.step(
                local,
                mandate,
                "REASSIGN_WORK",
                activity=str(activity_id),
                predecessor=str(bound["implementation"]),
                office=str(office_uuid),
            ),
            people,
        )
        while_assigned = support.database_now()
        self.assertEqual(
            committees.work_holder(activity, initial, "default"), worker.pk
        )

        def leave(person, place):
            return circles.leave(
                **(local | {"credential": person.credential}),
                participation_uuid=place.participation_uuid,
                request_uuid=uuid4(),
                confirmed=True,
            )

        def refused_after_return(person):
            with self.assertRaisesMessage(
                activities.ServiceActivityActorError,
                "This work has returned to the chair. Ask the chair to reassign it.",
            ):
                service.accept_service_assignment(
                    activities.AcceptServiceAssignmentCommand(**command(person))
                )

        worker_end = leave(worker, worker_place)
        while_returned = support.database_now()
        self.assertEqual(
            committees.work_holder(activity, initial, "default"), founder.pk
        )
        refused_after_return(worker)

        (board_chair,) = disc._rows(
            "SELECT public.cmt_board_chair(%s,statement_timestamp(),statement_timestamp()) AS office",
            [lo.pk],
        )
        self.assertIsNone(board_chair["office"])
        chair_end = leave(
            founder,
            models.CircleParticipation.objects.get(
                circle=circle, membership__identity=founder
            ),
        )
        elections.catch_up_terms(**general)
        elections.catch_up_terms(**general)
        self.assertIsNone(committees.work_holder(activity, initial, "default"))
        refused_after_return(founder)
        self.assertEqual(
            committees.work_holder(activity, initial, "default", while_assigned),
            worker.pk,
        )
        self.assertEqual(
            committees.work_holder(activity, initial, "default", while_returned),
            founder.pk,
        )

        records = disc._rows(
            """SELECT receipt, predecessor, holder, office, clause, cause, effective_at
                 FROM public.core_cmtcustody WHERE activity=%s ORDER BY effective_at,recorded_at""",
            [activity.pk],
        )
        original, assigned, returned, held = (
            records  # Extra returns or holds also fail here.
        )
        self.assertEqual(
            [row["receipt"] for row in records[:2]],
            [bound["implementation"], moved["implementation"]],
        )
        self.assertEqual(
            [row["predecessor"] for row in records],
            [None, original["receipt"], assigned["receipt"], returned["receipt"]],
        )
        self.assertEqual(
            [(row["holder"], row["office"]) for row in records],
            [
                (founder.pk, chair_id),
                (worker.pk, office.pk),
                (founder.pk, chair_id),
                (None, None),
            ],
        )
        self.assertEqual(
            [(row["cause"], row["effective_at"]) for row in (returned, held)],
            [
                ({"ending_office": office.pk}, worker_end.effective_at),
                ({"ending_office": chair_id}, chair_end.effective_at),
            ],
        )
        self.assertTrue(
            all(row["clause"]["binding"] == spec["terms"]["binding"] for row in records)
        )
        initial.refresh_from_db()
        self.assertEqual(initial.assignee_id, founder.pk)

        # The same Board can bind a different CSA directly to a constitutional
        # place. This trustee holds no SR office. The original CSA stays held.
        article = self.chair_article
        role_fixture = test_sr_office_actions.SROfficeActionTests()
        role_fixture.actor, role_fixture.organism = founder, lo
        # Ordinary personal ASSIGN is closed while voting is on. The constitution
        # expressly recognises this existing office, with the holder's own consent.
        chair_assignment = fx.office_proposal(lo, founder, worker, self.chair_role, self_proposed=True)
        at = support.database_now()
        claim = {
            "type": "OFFICE", "place": "CHAIR", "person": str(worker.identity_id),
            "provision": "Board chair", "version": article["version"], "binding": str(article["binding"]),
            "original_body": "Trustees", "original_date": at.date().isoformat(),
            "decision": "Board chair appointment minute", "evidence": "Signed Board register",
            "assignment": str(chair_assignment.assignment_uuid), "term_start": at.isoformat(), "term_end": None,
        }
        confirmed = elections.confirm_recognition(**(general | {"credential": worker.credential}),
                                                   claim=claim, request_uuid=uuid4())
        elections.recognise_incumbent(**general, claim=claim, consent_uuid=confirmed["act"], request_uuid=uuid4())
        board_place = models.CircleParticipation.objects.get(circle__circle_uuid=board["circle_uuid"], membership__identity=other)
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(membership=board_place.membership).exists())
        self.assertIsNone(committees.work_holder(activity, initial, "default"))
        activity_id = uuid4()
        seat = {"seat": board_place.seat_id}
        service.create_service_activity(activities.CreateServiceActivityCommand(
            **command(), service_version_pk=catalog.current_version_id,
            initiating_domain=activities.InitiatingDomain.SERVICE,
            initiating_domain_reference="board:" + str(board["circle_uuid"]),
            activity_basis_reference=str(adopted["binding_uuid"]),
        ))
        service.assign_service_activity(activities.AssignServiceActivityCommand(
            **command(), assignee_identity_id=worker.identity_id, assignment_reference="trustee-review",
            assignment_basis_reference=str(board_place.participation_uuid),
        ))
        direct = models.ServiceActivity.objects.get(activity_id=activity_id)
        original_assignment = models.ServiceActivityAssignment.objects.get(activity=direct)
        self.assertIsNone(committees.work_holder(direct, original_assignment, "default"))
        import json

        office_proposal = committees.attachment(
            organism_uuid=lo.organism_id, act="BIND_BOARD_WORK", target={"office": "CHAIR"}, activity=str(activity_id)
        )
        (office_plan,) = disc._rows(
            """SELECT public.cq3_plan(%s,b.board_uuid,%s,%s::jsonb,statement_timestamp())->'committee' AS work
                FROM public.core_discboard b WHERE b.organism_id=%s AND b.circle_id=%s""",
            [lo.pk, worker.pk, json.dumps(office_proposal), lo.pk, board_place.circle_id],
        )
        self.assertEqual((office_plan["work"]["target"], office_plan["work"]["office_id"]),
                         ({"office": "CHAIR"}, chair_assignment.pk))
        act = c.decide_instrument(self, board, committees.attachment(
            organism_uuid=lo.organism_id, act="BIND_BOARD_WORK", target=seat, activity=str(activity_id),
        ), people)
        def seat_work():
            (row,) = committees.board_work(**general, target=seat)
            self.assertEqual(row["activity_id"], activity_id)
            return row

        initial_board_work = seat_work()
        self.assertEqual((initial_board_work["receipt"], initial_board_work["holder"]), (act["implementation"], worker.identity_id))
        delegated = c.decide_instrument(self, board, committees.attachment(
            organism_uuid=lo.organism_id, act="REASSIGN_BOARD_WORK", target=seat, activity=str(activity_id),
            predecessor=str(act["implementation"]), assignee_target=seat, occupancy=str(board_place.participation_uuid),
        ), people)
        assigned_board_work = seat_work()
        self.assertEqual((assigned_board_work["receipt"], assigned_board_work["holder"]), (delegated["implementation"], other.identity_id))
        (office_target,) = disc._rows(
            """SELECT person,office FROM public.cmt_board_occupant(%s,'{"office":"CHAIR"}',
                        statement_timestamp(),statement_timestamp())""", [board_place.circle_id],
        )
        self.assertEqual((office_target["person"], office_target["office"]), (worker.pk, chair_assignment.pk))
        while_seated = support.database_now()
        notice = elections.resign_board_place(
            **(general | {"credential": other.credential}), participation_uuid=board_place.participation_uuid,
            binding_uuid=article["binding"], version_id=article["version"], provision="Trustee notice", request_uuid=uuid4(),
        )
        self.assertEqual(notice["effect"], "DATED")
        end = models.CircleParticipationEnd.objects.get(participation=board_place)
        self.assertEqual(seat_work()["holder"], worker.identity_id)
        self.assertEqual(committees.work_holder(direct, original_assignment, "default", while_seated), other.pk)
        refused_after_return(other)
        roles.transition_assignment(role_fixture._transition_command(chair_assignment, "END", actor=worker))
        self.assertIsNone(seat_work()["holder"])
        refused_after_return(worker)
        chain = disc._rows(
            """SELECT receipt,predecessor,place,office,holder,clause,effective_at FROM public.core_cmtcustody
               WHERE activity=%s ORDER BY effective_at,recorded_at""", [direct.pk],
        )
        chair_row, seat_row, received, held_again = chain
        self.assertEqual([x["predecessor"] for x in chain], [None, chair_row["receipt"], seat_row["receipt"], received["receipt"]])
        self.assertEqual((chair_row["office"], chair_row["holder"]), (chair_assignment.pk, worker.pk))
        self.assertEqual(seat_row["place"], board_place.pk)
        self.assertEqual((received["office"], received["holder"], received["effective_at"]),
                         (chair_assignment.pk, worker.pk, end.effective_at))
        self.assertEqual((held_again["office"], held_again["holder"], seat_work()["receipt"]),
                         (None, None, held_again["receipt"]))
        self.assertTrue(all(x["clause"]["binding"] == mandate for x in chain))

    def test_task_completion_ends_delegation_and_preserves_history(self):
        """C-06. Board task completion ends delegated authority during a qualifying vote."""
        lo, general, board, voters = self.board(subscription=True)
        founder = voters[0]
        parent = self.committee(general)
        parent_formed_child = self.committee(general)
        board_formed_child = self.committee(general)
        parent_scope = general | {"circle_uuid": parent.circle_uuid}
        for voter in voters[1:]:
            c.join(general, parent, voter)
        instrument, _ = c.mandate(
            lo,
            founder,
            parent,
            "1",
            kind="AD_HOC",
            task="reserve review",
            completion_body="GOVERNANCE",
            terms={"form_children": True},
            publisher=c.first_version,
        )
        formed = c.decide_instrument(self, board, instrument, voters)
        binding = instrument["spec"]["proposal"]

        # Keep a real report and its own exact result for the authorised read after closure.
        report = c.step(
            parent_scope,
            binding,
            "REPORT",
            area="reserves",
            finding="Reserves reviewed.",
        )
        reported = c.decide_instrument(self, parent_scope, report, voters)
        (report_key,) = disc._rows(
            "SELECT thread_uuid FROM public.core_cqact WHERE act_uuid=%s",
            [reported["implementation"]],
        )

        children = []
        child_bindings = []
        for circle, authority, expected_grant in (
            (parent_formed_child, parent_scope, binding),
            (board_formed_child, board, None),
        ):
            child, _ = c.mandate(
                lo,
                founder,
                circle,
                "3",
                kind="SUB_COMMITTEE",
                parent=str(parent.circle_uuid),
                publisher=c.first_version,
            )
            receipt = c.decide_instrument(self, authority, child, voters)
            mandate = receipt["plan"]["committee"]
            self.assertEqual(
                (mandate["parent_binding"], mandate["delegating_parent"]),
                (binding, expected_grant),
            )
            children.append(general | {"circle_uuid": circle.circle_uuid})
            child_bindings.append(child["spec"]["proposal"])
        self.assertEqual(
            disc._rows(
                """SELECT public.cmt_live(binding,statement_timestamp(),statement_timestamp()) AS live
                          FROM unnest(%s::uuid[]) AS t(binding)""",
                [child_bindings],
            ),
            [{"live": True}, {"live": True}],
        )

        outside, wrong_version = c.first_version(
            founder, {"committee_adoption": {"mandate": binding, "area": "hiring"}}
        )
        self.refusal(parent_scope, c.instrument(lo, outside, wrong_version), "CMT_AREA")
        doc, version = c.first_version(
            founder,
            {
                "committee_adoption": {"mandate": binding, "area": "reserves"},
                "text": "Use the reserve.",
            },
        )
        proposed = c.instrument(lo, doc, version)
        # Three qualified answers meet both thresholds before the task ends.
        # The fourth member keeps the ballot open, then votes against the proposal.
        last_voter = law.add_member(lo)
        c.join(general, parent, last_voter)
        pending = c.start(self, parent_scope, proposed)
        cq.mark_prepared(**pending, request_uuid=uuid4())
        (opened,) = disc._rows(
            """SELECT act_uuid FROM public.core_cqact
                WHERE thread_uuid=%s AND operation IN ('OPEN_VOTE','PROCEED','REOPEN_VOTE')""",
            [pending["thread_uuid"]],
        )
        ballot = opened["act_uuid"]
        for voter in voters:
            fx.cast(pending, ballot, voter)
        (live_vote,) = disc._rows(
            """SELECT count(*) AS eligible, count(*) FILTER (WHERE choice='QUALIFIED') AS yes,
                      public.cmt_live(%s,statement_timestamp(),statement_timestamp()) AS live
               FROM public.cq_ballot_entries(%s,statement_timestamp())""",
            [binding, ballot],
        )
        self.assertEqual(live_vote, {"eligible": 4, "yes": 3, "live": True})
        completed = c.decide_instrument(
            self,
            board,
            c.step(board, binding, "COMPLETE_TASK", task="reserve review"),
            voters,
        )
        self.assertEqual(completed["plan"]["committee"]["completed"], binding)
        fx.cast(pending, ballot, last_voter, choice="NOT_QUALIFIED")
        (settlement,) = disc._rows(
            """SELECT act_uuid, facts->'count' AS count,
                      facts->>'disposition' AS disposition, facts->>'reason' AS reason
                 FROM public.core_cqact
                WHERE thread_uuid=%s AND operation='SETTLE' AND request->>'ballot'=%s""",
            [pending["thread_uuid"], str(ballot)],
        )
        self.assertEqual(
            (
                settlement["count"]["kind"],
                settlement["count"]["required"],
                settlement["count"]["qualified"],
                settlement["count"]["not_qualified"],
                settlement["disposition"],
                settlement["reason"],
            ),
            ("QUALIFIED", 3, 3, 1, "NO_EFFECT", cq.MESSAGES["CMT_ENDED"]),
        )
        self.assertIsNone(
            cq_reads.implementation_record(
                **pending, result_uuid=settlement["act_uuid"]
            )["effect"]
        )
        (laws,) = disc._rows(
            "SELECT count(*) AS n FROM public.core_organismlawbinding WHERE binding_uuid=%s OR version_id=%s",
            [proposed["spec"]["proposal"], version.pk],
        )
        self.assertEqual(laws["n"], 0)

        (parent_history,) = committees.history(**parent_scope)
        self.assertEqual(
            (parent_history["implementation"], parent_history["in_force"]),
            (formed["implementation"], False),
        )
        dependent, independent = (committees.history(**scope)[0] for scope in children)
        self.assertEqual(
            (dependent["in_force"], independent["in_force"]), (False, True)
        )
        kept = cq_reads.implementation_record(
            **parent_scope,
            thread_uuid=report_key["thread_uuid"],
            result_uuid=reported["result"],
        )["effect"]
        self.assertEqual(
            (
                kept["implementation"],
                kept["committee"]["act"],
                kept["committee"]["finding"],
            ),
            (reported["implementation"], "REPORT", "Reserves reviewed."),
        )

    def test_members_stand_down_board_and_recover_its_authority(self):
        """C108-03. One members' result ends the Board and returns its powers and work."""
        from dataclasses import replace
        from uuid import UUID

        from src.intevia.services import organism_role_definition_service as definitions
        from src.intevia.services import service_activity_service as activities
        from src.intevia.services.organism_office_consent_service import (
            OfficeAppointmentConsentService,
        )
        from src.intevia.services.organism_role_service import OrganismRoleService
        from tests import (
            cq_authority_support,
            test_sr_office_actions,
            test_sr_office_guards,
        )

        due = support.database_now() + timedelta(days=1)  # Fixture date, still future at settlement.
        committee_id = uuid4()
        article = {}
        secretary = None

        def cite(lo, founder, binding, version):
            nonlocal secretary
            article.update(binding=str(binding), version=version)
            # The LO Coordinator can appoint the Secretary here, before voting opens.
            helper = test_sr_office_actions.SROfficeActionTests()
            helper.actor, helper.organism = founder, lo
            definition = definitions.DefinitionContent(code="SECRETARY", provisions=(
                definitions.DefinitionProvision("OFFICE_BASIS", "Secretary", str(version)),))
            authority = cq_authority_support.authority()
            source = definitions.OrganismRoleDefinitionService(authority=authority, clock=support.database_now)
            defined = source.record_role_definition(helper._command(content=definition))
            role = models.OrganismRoleDefinition.objects.get(role_uuid=defined.role_definition_uuid)
            secretary = fx.office_proposal(lo, founder, founder, role, self_proposed=True)
            OrganismRoleService(authority=authority, clock=support.database_now).transition_assignment(
                helper._transition_command(secretary, "ASSIGN"))

        lo, general, board, voters = c.formation_board(
            self, offering="FREE", after_article=cite, charter_fields={
                "offices": {"Secretary": "The Secretary keeps the minutes."},
                "resignations": {"Notice": {"place": "trustee-1", "effect": "DATED", "effective_at": due.isoformat()}},
                "elections": {"Treasurer": {"type": "OFFICE", "body": "GOVERNANCE", "place": "TREASURER"}},
                "authority_transfers": {"Delegate": {"grantor": "GOVERNANCE", "power": "RUN_ELECTION",
                    "election": "Treasurer", "recipients": [str(committee_id)]}},
            },
        )
        founder = voters[0]
        gov = models.Circle.objects.get(circle_uuid=board["circle_uuid"])
        circle = circles.form(**general, circle_uuid=committee_id, request_uuid=uuid4(), kind="GENERIC",
                              name="Reserves committee", purpose="Review reserves and administer the Treasurer election.")
        local = general | {"circle_uuid": committee_id}
        # The worker holds the committee office but never the LO Coordinator office.
        worker = voters[1]
        place = c.join(general, circle, worker)
        c.join(general, circle, voters[2])
        authority = cq_authority_support.authority()
        roles = OrganismRoleService(authority=authority, clock=support.database_now)
        office_id = uuid4()
        roles.nominate_circle_coordinator(**local, participation_uuid=place.participation_uuid,
                                          assignment_uuid=office_id, request_uuid=uuid4())
        appointment = models.ContextualRoleAssignment.objects.get(assignment_uuid=office_id)
        consent = test_sr_office_actions.SROfficeActionTests()._consent_command(place.membership, appointment)
        OfficeAppointmentConsentService(authority=authority, clock=support.database_now).record_office_consent(consent)
        roles.appoint_circle_coordinator(**local, assignment_uuid=office_id, request_uuid=uuid4())
        documents = []

        def publish(author, contents):
            pair = c.first_version(author, contents)
            documents.append(pair)
            return pair

        scope = article | {"power": "RUN_ELECTION", "provision": "Treasurer", "place": "TREASURER"}
        work = {"committee_work": {"return_to": "CHAIR"}}
        forming, _ = c.mandate(
            lo, founder, circle, "1", publisher=publish, chair=worker, terms=work, policy_fields=work | {
                "authority_transfer": {"scope": scope, "citation": article | {"provision": "Delegate"},
                    "from": "GOVERNANCE", "to": str(committee_id), "predecessor": None}},
        )
        binding = forming["spec"]["proposal"]
        grant = c.decide_instrument(self, board, forming, voters)
        self.assertEqual(elections.power_history(**general, scope=scope)[-1]["holder"], str(committee_id))
        activity_id, service = c.assigned_activity(founder, circle, binding, worker=worker, with_service=True)
        bound = c.decide_instrument(self, local, c.step(local, binding, "BIND_WORK", activity=str(activity_id)), voters)

        places = dict(models.CircleParticipation.objects.filter(circle=gov).values_list("pk", "participation_uuid"))
        offices = set(models.ContextualRoleAssignment.objects.filter(circle=gov).values_list("pk", flat=True))
        self.assertEqual(len(places), 3)
        self.assertTrue(offices)
        offices.add(secretary.pk)
        office_events = models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=secretary)
        award = office_events.get(action="ASSIGN")
        # A physical, prospective END on an existing assignment. Refresh the cache
        # at receipt, while the later END is already the newest recorded chain link.
        with transaction.atomic(), connection.cursor() as cur:
            scheduled_pk = test_sr_office_guards._lo_transition(
                cur, {"lo": lo.pk, "basis": lo.founding_authority_basis_id},
                enterer_id=founder.pk, enterer_membership_id=secretary.membership_id,
                assignment_id=secretary.pk, assignment_uuid=secretary.assignment_uuid,
                predecessor_id=award.pk, sequence=award.sequence + 1,
                action="END", prior_state="ACTIVE", resulting_state="ENDED",
                at=support.database_now(), effective_at=due, tag="c108-secretary-" + uuid4().hex)
        scheduled = office_events.get(pk=scheduled_pk)
        self.assertEqual((scheduled.action, scheduled.effective_at), ("END", due))
        self.assertLess(scheduled.recorded_at, due)
        self.assertEqual(law.query_one("SELECT ending FROM public.elect_vacancy_sources WHERE ending=%s",
                                     [scheduled.event_uuid]), scheduled.event_uuid)
        seat = models.CircleParticipation.objects.get(circle=gov, seat_id="trustee-1", membership__identity=worker)
        notice = elections.resign_board_place(**(general | {"credential": worker.credential}),
            participation_uuid=seat.participation_uuid, binding_uuid=UUID(article["binding"]),
            version_id=article["version"], provision="Notice", request_uuid=uuid4())
        self.assertEqual(notice["effect"], "DATED")
        original = models.CircleParticipationEnd.objects.get(participation=seat, composed_from_event_uuid=notice["act"])
        self.assertEqual(original.effective_at, due)
        known_before = support.database_now()
        activity = models.ServiceActivity.objects.get(activity_id=activity_id)
        assignment = models.ServiceActivityAssignment.objects.get(activity=activity)
        self.assertEqual(committees.work_holder(activity, assignment, "default", person=worker.pk), worker.pk)
        life = models.CircleStateEvent.objects.get(circle=gov, action="ACTIVATE")
        att = cq.stand_down_attachment(organism_uuid=lo.organism_id, circle_uuid=gov.circle_uuid,
                                       lifecycle_uuid=life.event_uuid, board_uuid=disc._board_id(lo.organism_id, None))
        decision = c.decide_instrument(self, general, att, voters)
        result = decision["result"]
        marker = "c108-stand-down:" + str(result)
        edge = models.CircleStateEvent.objects.get(circle=gov, evidence_reference=marker)
        self.assertLess(edge.effective_at, original.effective_at)
        self.assertIn(original.pk, decision["plan"]["targets"]["superseded_place_endings"])
        self.assertIn(str(scheduled.event_uuid), decision["plan"]["targets"]["superseded_office_endings"])
        self.assertLess(scheduled.recorded_at, edge.effective_at)
        down = office_events.get(action="END", evidence_reference=marker)
        self.assertEqual((down.predecessor_id, down.sequence, down.effective_at, down.prior_state,
                          down.resulting_state, down.evidence_reference),
                         (scheduled.pk, scheduled.sequence + 1, edge.effective_at, "ACTIVE", "ENDED", marker))

        def office_history():
            with connection.cursor() as cur:  # l1_commitment is database-assigned and not on the model.
                cur.execute("""SELECT event_uuid,sequence,predecessor_id,action,resulting_state,effective_at,
                    recorded_at,evidence_reference,l1_commitment FROM public.core_contextualroleassignmentstateevent
                    WHERE assignment_id=%s ORDER BY sequence""", [secretary.pk])
                return cur.fetchall()

        before_replay = office_history()
        (answer,) = disc._rows("""SELECT a.thread_uuid,a.act_uuid,a.request->>'ballot' AS ballot
            FROM public.core_cqact a JOIN public.core_cqact r ON r.act_uuid=%s
            WHERE a.thread_uuid=r.thread_uuid AND a.operation='ANSWER' AND a.actor_id=%s
              AND a.request->>'ballot'=r.request->>'ballot'""", [result, voters[-1].pk])
        self.assertEqual(cq.answer(**(general | {"credential": voters[-1].credential}), thread_uuid=answer["thread_uuid"],
            ballot_uuid=UUID(answer["ballot"]), request_uuid=answer["act_uuid"], choice="QUALIFIED"), answer["act_uuid"])
        self.assertEqual(office_history(), before_replay)
        self.assertEqual(list(office_events.filter(action="END").order_by("sequence").values_list(
            "event_uuid", "effective_at", "recorded_at", "evidence_reference")),
            [(scheduled.event_uuid, due, scheduled.recorded_at, scheduled.evidence_reference),
             (down.event_uuid, edge.effective_at, down.recorded_at, marker)])
        self.assertEqual(list(models.CircleParticipationEnd.objects.filter(participation=seat)
            .order_by("effective_at").values_list("effective_at", "composed_from_event_uuid")),
            [(edge.effective_at, result), (due, notice["act"])])
        self.assertEqual(law.query_one("SELECT public.elect_place_end(%s,%s)", [seat.pk, known_before]), due)
        self.assertEqual(law.query_one("SELECT public.elect_place_end(%s,statement_timestamp())", [seat.pk]), edge.effective_at)
        self.assertEqual((edge.action, edge.prior_state, edge.resulting_state), ("STAND_DOWN", "ACTIVE", "DORMANT"))
        self.assertEqual(law.query_one("SELECT public.cq3_board(%s,statement_timestamp(),statement_timestamp())",
                                       [lo.pk]), None)
        self.assertCountEqual(models.CircleParticipationEnd.objects.filter(
            composing_rule_reference=marker, composed_from_event_uuid=result, effective_at=edge.effective_at
        ).values_list("participation_id", flat=True), places)
        ended = dict(models.ContextualRoleAssignmentStateEvent.objects.filter(
            evidence_reference=marker, action="END", resulting_state="ENDED", effective_at=edge.effective_at
        ).values_list("assignment_id", "event_uuid"))
        self.assertEqual(set(ended), offices)
        self.assertCountEqual([row["identity_id"] for row in disc._rows(
            """SELECT identity_id FROM public.core_organismmembership WHERE living_organism_id=%s
            AND public.disc_member_state(%s,identity_id,statement_timestamp())='ACTIVE'""", [lo.pk, lo.pk]
        )], [v.pk for v in voters])
        self.assertEqual(decision["plan"]["targets"]["mandates"], [str(binding)])
        self.assertFalse(law.query_one("SELECT public.cmt_live(%s,statement_timestamp(),statement_timestamp())", [binding]))
        self.assertTrue(law.query_one("""SELECT EXISTS(SELECT FROM public.l_laws_at(%s,statement_timestamp(),
            statement_timestamp()) WHERE binding=%s)""", [lo.organism_id, binding]))
        history = elections.power_history(**general, scope=scope)
        self.assertEqual([(r["result"], r["holder"]) for r in history],
                         [(grant["result"], str(committee_id)), (result, "LO_MEMBERSHIP")])
        self.assertEqual(history[-1]["predecessor"], str(grant["implementation"]))
        (receipt,) = disc._rows("SELECT receipt,cause FROM public.core_cmtcustody WHERE predecessor=%s",
                                [bound["implementation"]])
        self.assertEqual(receipt["cause"], {"stand_down": str(result), "custodian": "LO_COORDINATORS", "organism": lo.pk})
        self.assertEqual(committees.work_holder(activity, assignment, "default", person=founder.pk), founder.pk)
        self.assertIsNone(committees.work_holder(activity, assignment, "default", person=voters[1].pk))
        expected = ("This work passed to the LO Coordinator(s) when the Board stood down. "
                    "This request cannot change it. Its custody record is unchanged. "
                    "Ask an LO Coordinator to pick it up or arrange reassignment.")
        with self.assertRaisesMessage(activities.ServiceActivityActorError, expected):
            service.accept_service_assignment(activities.AcceptServiceAssignmentCommand(
                credential=worker.credential, request_reference=uuid4().hex, idempotency_key=uuid4().hex,
                occurred_at=support.database_now(), activity_id=activity_id))
        with self.assertRaisesMessage(cq.CQRefused, expected):
            c.start(self, local, c.step(local, binding, "REASSIGN_WORK", activity=str(activity_id),
                predecessor=str(receipt["receipt"]), office=str(office_id)))
        self.assertFalse(disc._rows("SELECT receipt FROM public.core_cmtcustody WHERE predecessor=%s", [receipt["receipt"]]))
        elections.catch_up_vacancies(credential=founder.credential)
        for table in ("elect_vacancy_sources", "core_electvacancy"):
            self.assertEqual(law.query_one("SELECT count(*) FROM public." + table +
                " WHERE organism_id=%s AND ending=ANY(%s::uuid[])", [lo.pk, [*places.values(), *ended.values(), scheduled.event_uuid]]), 0)
        # Retire the Board-adopted Policy by the pre-GOVERNANCE route. The Board's original act survives.
        doc, version = documents[-1]
        retire = replace(law.adoption(lo, doc), operation="RETIRE", intent="RETIRE", predecessor=UUID(str(binding)),
                         version=version.version_number).content()
        changed = c.decide_instrument(self, general, {"kind": "LAW", "text": version.content, "spec": retire}, voters)
        self.assertEqual(changed["plan"]["route"], "CQ_MEMBERSHIP")
        self.assertFalse(law.query_one("""SELECT EXISTS(SELECT FROM public.l_laws_at(%s,statement_timestamp(),
            statement_timestamp()) WHERE binding=%s)""", [lo.organism_id, binding]))
        current = cq.settings(**general)
        off = cq.settings_attachment(organism_uuid=lo.organism_id, predecessor=current["version"], changes={"enabled": False})
        c.decide_instrument(self, general, off, voters)
        self.assertIs(cq.settings(**general)["settings"]["enabled"], False)

    def test_members_restore_the_same_board_on_its_new_elected_terms(self):
        """C1-108-R. A restoration vote authorises the same Board only after its constitutional seats are filled."""
        import json
        from datetime import datetime

        self.topic_category_ids = {}
        # Three founding/appointed seats and three distinct seats reserved for election.
        lo, founder = law.ordinary_birth(offering_choice="FREE", board_seats=6, elected=(3, 4, 5), board_minimum=None)
        member_board = disc._board_id(lo.organism_id, None)
        origin = support.database_now()
        rules = {f"Seat {n}": fx.seat(place=f"trustee-{n}", term={"end": (origin + timedelta(days=n)).isoformat()})
                 for n in (3, 4, 5)}
        text = {"board_makeup": {"minimum": 3}, "board_restoration": {"Restore": {"body": str(member_board)}},
                "committees": {"chair_board_member": False}, "elections": rules}
        charter = law.Document(founder, content=json.dumps(text, sort_keys=True))
        proposal = law.adoption(lo, charter, "ARTICLE")
        self.assertEqual(law.law.OrganismLawService().adopt_law(law.command(founder, proposal)).status, "ADOPT")
        citation = {"binding_uuid": proposal.proposal, "version_id": charter.first.pk}
        provision = {"binding": str(proposal.proposal), "version": charter.first.pk, "provision": "Restore"}

        _, general, board, people = self.activate_board(lo, founder)
        gov = models.Circle.objects.get(circle_uuid=board["circle_uuid"])
        first = models.CircleStateEvent.objects.get(circle=gov, action="ACTIVATE")
        committee = self.committee(general)
        forming, _ = c.mandate(lo, founder, committee, "1", publisher=c.first_version)
        formed = c.decide_instrument(self, board, forming, people)
        binding = forming["spec"]["proposal"]
        self.assertTrue(formed["plan"]["committee"])

        def stand_down(tenure):
            return cq.stand_down_attachment(organism_uuid=lo.organism_id, circle_uuid=gov.circle_uuid,
                                            lifecycle_uuid=tenure, board_uuid=member_board)

        down = c.decide_instrument(self, general, stand_down(first.event_uuid), people)["result"]
        att = cq.restoration_attachment(organism_uuid=lo.organism_id, circle_uuid=gov.circle_uuid,
                                        stand_down_uuid=down, board_uuid=member_board, citation=provision)
        off_att = cq.settings_attachment(organism_uuid=lo.organism_id, predecessor=cq.settings(**general)["version"],
                                         changes={"enabled": False})
        c.decide_instrument(self, general, off_att, people)
        with self.assertRaisesMessage(cq.CQRefused, cq.MESSAGES["CQ_RESTORE_OFF"]):
            c.start(self, general, att)
        enabled = cq.change_settings(**general, request_uuid=uuid4(), predecessor=cq.settings(**general)["version"],
                                     changes={"enabled": True})
        grant = c.decide_instrument(self, general, att, people)
        self.assertEqual((grant["plan"]["domain"], grant["plan"]["stand_down"], grant["plan"]["citation"]),
                         ("BOARD_RESTORE", str(down), provision))
        def read_effect():
            thread = law.query_one("SELECT thread_uuid FROM public.core_cqact WHERE act_uuid=%s", [grant["result"]])
            return cq_reads.implementation_record(**general, thread_uuid=thread, result_uuid=grant["result"])["effect"]

        before = read_effect()
        self.assertEqual(before["restoration_decision"]["citation"], provision)
        self.assertNotIn("restored_board", before)
        activation = general | {"circle_uuid": gov.circle_uuid, "request_uuid": uuid4(),
                                "restoration_result_uuid": grant["result"]}
        awards = {}
        for n, person in zip((3, 4, 5), people, strict=True):
            if n == 5:
                with self.assertRaisesMessage(circles.CircleAccessRefused, "The Board remains inactive."):
                    circles.activate(**activation)
                self.assertIsNone(law.query_one("SELECT public.cq3_board(%s,statement_timestamp(),statement_timestamp())",
                                                [lo.pk]))
            election = fx.call(self, general, citation, provision=f"Seat {n}", place=f"trustee-{n}")
            fx.nominate(election, person)
            ballot = fx.open_election(election)
            for voter in people:
                fx.cast(election, ballot, voter)
            win = cq_reads.vote_status(**election)["result_uuid"]
            awards[f"trustee-{n}"] = (str(person.identity_id), str(win), (origin + timedelta(days=n)))

        pending = c.start(self, general, cq.settings_attachment(
            organism_uuid=lo.organism_id, predecessor=enabled, changes={"enabled": False}))
        self.assertEqual(circles.activate(**activation), activation["request_uuid"])
        self.assertEqual(circles.activate(**activation), activation["request_uuid"])
        gov.refresh_from_db()
        self.assertEqual((gov.state, gov.head_event.event_uuid), ("ACTIVE", activation["request_uuid"]))
        record = read_effect()["restored_board"]
        facts = record["facts"]
        self.assertEqual((record["activation"], record["enterer"], facts["result"], facts["stand_down"],
                          facts["circle"], facts["citation"]), (str(activation["request_uuid"]), str(founder.identity_id),
                          str(grant["result"]), str(down), str(gov.circle_uuid), provision))
        self.assertEqual(facts["makeup"]["minimum_sources"][0]["binding"], str(proposal.proposal))
        self.assertEqual(facts["makeup"]["minimum"], 3)
        self.assertNotEqual(grant["result"], activation["request_uuid"])
        decision_at = law.query_one("SELECT effective_at FROM public.core_cqact WHERE act_uuid=%s", [grant["result"]])
        self.assertLessEqual(decision_at, datetime.fromisoformat(record["effective_at"]))
        self.assertEqual({x["place"]: (x["person"], x["decision"], datetime.fromisoformat(x["end"]))
                          for x in facts["seats"]}, awards)
        for seat in facts["seats"]:
            self.assertEqual((seat["admission"], seat["binding"], seat["version"]),
                             ("ELECTION", str(proposal.proposal), charter.first.pk))
            self.assertLess(datetime.fromisoformat(seat["start"]), datetime.fromisoformat(seat["end"]))
        self.assertEqual(cq.phase(**pending)["phase"], "CLOSED")
        self.assertFalse(law.query_one("SELECT public.cmt_live(%s,statement_timestamp(),statement_timestamp())", [binding]))
        with self.assertRaises(cq.CQRefused):
            c.start(self, general, stand_down(first.event_uuid))
        self.assertEqual(law.query_one("SELECT public.c108_active_lifecycle(%s,statement_timestamp(),statement_timestamp())",
                                      [gov.pk]), activation["request_uuid"])
