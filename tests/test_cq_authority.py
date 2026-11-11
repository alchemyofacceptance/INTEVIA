"""DISC3-01–08. Lawful first activation and one binding effect per exact CQ result."""

from contextlib import contextmanager
from dataclasses import replace
from importlib import import_module
from uuid import UUID, uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_commands as circles
from src.intevia.services import (
    cq,
    cq_reads,
    cq_voting_state,
    law_acts,
    library_laws,
    library_policy,
)
from src.intevia.services import discussion as disc
from src.intevia.services import discussion_posts as posts
from src.intevia.services import guiding_intentions as gi
from tests import cq_authority_support as board_support
from tests import cq_settings_support as motions
from tests import s015_0021_support as support
from tests import test_cq_clarity as clarity
from tests import test_cq_decisions as decisions
from tests import test_guiding_intentions as gi_fixture
from tests import test_law_acts as law

BOARD_VOTING_MESSAGE = (
    "Switching voting off was refused. Voting stays on while the Board is active. "
    "Nothing changed. Put Board stand-down to the members or the body named in the "
    "constitution. Your LO Coordinator(s) can help."
)


class CQAuthorityTests(TransactionTestCase):
    topic = clarity.CQClarityTests.topic
    configure = clarity.CQClarityTests.configure
    facts = clarity.CQClarityTests.facts
    cast = decisions.CQDecisionTests.cast
    closed = decisions.CQDecisionTests.closed

    @contextmanager
    def exact_refusal(self, error_type, message):
        with self.assertRaises(error_type) as refused:
            yield
        self.assertEqual(str(refused.exception), message)

    ordinary = staticmethod(board_support.ordinary)

    def attachment(self, lo, document, cls="ARTICLE"):
        proposal = law.adoption(lo, document, law_class=cls)
        return {
            "kind": "LAW",
            "spec": proposal.content(),
            "text": document.first.content,
        }

    def motion(self, scope, attachment, title="Adopt the attached instrument", *, category_id=None, suggestion=None):
        """Put forward a distinct motion or the same Suggestion after its refused attempt."""
        if suggestion is None:
            journey, _, topic = self.topic(scope, category_id=category_id, title=title, markdown=title)
        else:
            journey, _, topic = suggestion
        act = cq.put_forward(
            **journey,
            request_uuid=uuid4(),
            version_uuid=topic["version"],
            attachment=attachment,
        )
        self.assertEqual(cq.phase(**journey)["attachment"], attachment)
        return journey, topic["version"], act

    def ready(self, journey, version, people, officer=None):
        managed = (
            journey if officer is None else journey | {"credential": officer.credential}
        )
        cq.mark_prepared(**managed, request_uuid=uuid4())
        for member in people:
            cq.confirm_clarity(
                **(journey | {"credential": member.credential}),
                request_uuid=uuid4(),
                version_uuid=version,
            )
        return managed

    def ballot(
        self,
        scope,
        attachment,
        people,
        *,
        officer=None,
        category_id=None,
        suggestion=None,
        title="Adopt the attached instrument",
    ):
        journey, version, first = self.motion(scope, attachment, title, category_id=category_id, suggestion=suggestion)
        managed = journey if officer is None else journey | {"credential": officer.credential}
        ballot = motions.open_motion(managed, version, people)
        self.assertEqual(
            self.facts(ballot)["facts"]["attachment_fingerprint"],
            self.facts(first)["facts"]["attachment_fingerprint"],
        )
        return journey, ballot

    def qualification(self, journey, ballot, people):
        for person in people:
            self.cast(journey, ballot, person)
        result = self.closed(journey, ballot)
        self.assertEqual(result["count"]["kind"], "QUALIFIED")
        return result

    def binding_count(self, attachment):
        return law.query_one(
            "SELECT count(*) FROM public.core_organismlawbinding WHERE binding_uuid=%s",
            [attachment["spec"]["proposal"]],
        )

    def test_first_activation_requires_birthing_voting_and_three_seats(self):
        """DISC3-01. First activation proves the seated trustees satisfy their own operative law."""
        lo, founder = law.ordinary_birth(offering_choice="FREE", board_seats=5, board_minimum=None)
        circle = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        scope = {"credential": founder.credential, "organism_uuid": lo.organism_id}
        request = scope | {"circle_uuid": circle.circle_uuid, "request_uuid": uuid4()}
        board_support.seat_trustee(lo, founder, circle, 1)
        version = self.configure(scope, {"enabled": True})
        refusal = (
            "The Birthing choice is recorded. Activation still needs operative voting."
        )
        switched = self.configure(scope, {"enabled": False}, version)
        for seat in (2, 3):
            board_support.seat_trustee(lo, founder, circle, seat)
        with self.exact_refusal(circles.CircleAccessRefused, refusal):
            circles.activate(**request)
        enabled = self.configure(scope, {"enabled": True}, switched)
        silent_at = support.database_now()
        # Four real trustees would have passed the old hard-coded three.
        with self.exact_refusal(
            circles.CircleAccessRefused,
            "GOVERNANCE could not be activated. No local Article, Bylaw or Policy in force states the Board’s minimum "
            "number of trustees. The Board remains inactive. State the minimum in the constitution, a Bylaw or a Policy, "
            "with the maximum if one applies. Ask the LO Coordinator(s) to arrange adoption and try again.",
        ):
            circles.activate(**request)
        outside_range = (
            "GOVERNANCE could not be activated. The eligible trustees in distinct constitutional seats do not meet "
            "the Board size stated in the governing law. The Board remains inactive. Check the seated trustees against "
            "that provision and try again. The LO Coordinator(s) can help with the seat records."
        )
        # Four exceed a Policy ceiling of three while GOVERNANCE is dormant.
        policy, policy_version = law.adopt_board_makeup(lo, founder, 3, 3, law_class="POLICY")
        with self.exact_refusal(circles.CircleAccessRefused, outside_range):
            circles.activate(**request)
        policy_at = support.database_now()

        # The same four members adopt an Article requiring five.
        self.topic_category_ids = {}
        document = law.board_makeup_document(founder, 5, 5)
        article = self.attachment(lo, document)
        people = motions.voters(scope)
        self.assertEqual(len(people), 4)
        making, law_ballot = self.ballot(scope, article, people)
        self.assertEqual(self.qualification(making, law_ballot, people)["disposition"], "APPLIED")
        binding = article["spec"]["proposal"]

        def makeup(at):
            return disc._rows("SELECT public.c108_board_makeup(%s,%s,statement_timestamp()) AS rule",
                              [lo.pk, at])[0]["rule"]

        self.assertEqual(makeup(silent_at)["problem"], "CIRCLE_G4_MAKEUP_MISSING")
        previous = makeup(policy_at)
        self.assertEqual((previous["minimum"], previous["maximum"], previous["minimum_sources"][0]["binding"],
                          previous["minimum_sources"][0]["version"]), (3, 3, str(policy), policy_version))
        current = makeup(support.database_now())
        source = {"binding": str(binding), "version": document.first.pk,
                  "version_number": document.first.version_number, "class": "ARTICLE"}
        self.assertEqual((current["minimum"], current["maximum"], current["minimum_sources"], current["maximum_sources"]),
                         (5, 5, [source], [source]))
        with self.exact_refusal(circles.CircleAccessRefused, outside_range):
            circles.activate(**request)
        self.assertFalse(models.CircleStateEvent.objects.filter(circle=circle, action="ACTIVATE").exists())
        board_support.seat_trustee(lo, founder, circle, 4)
        # The five seated voters share the same activation across the three Off motions.
        pending, _ = motions.propose(self, scope, {"enabled": False}, enabled)
        clarity_motion, _ = motions.propose(self, scope, {"enabled": False}, enabled)
        cq.mark_prepared(**clarity_motion, request_uuid=uuid4())
        voting, wording = motions.propose(self, scope, {"enabled": False}, enabled)
        ballot = motions.open_motion(voting, wording, motions.voters(scope))
        other_motion, _, post = self.topic(scope)
        cq.put_forward(**other_motion, request_uuid=uuid4(), version_uuid=post["version"])
        off_motions = (pending, clarity_motion, voting)
        self.assertEqual([cq.phase(**motion)["phase"] for motion in off_motions],
                         ["PREPARATION", "CLARITY", "VOTING"])
        circle.refresh_from_db()
        with (
            self.assertRaisesRegex(IntegrityError, "CIRCLE_G4_AUTHORITY"),
            transaction.atomic(),
        ):
            circles.write_event(
                "default",
                circle,
                founder,
                "ACTIVATE",
                "ACTIVE",
                circle.founding_reference,
                "unsupported-Birthing-claim",
                uuid4(),
                support.database_now(),
            )
        self.assertEqual([cq.phase(**motion)["phase"] for motion in off_motions],
                         ["PREPARATION", "CLARITY", "VOTING"])
        self.assertEqual(circles.activate(**request), request["request_uuid"])
        self.assertEqual(circles.activate(**request), request["request_uuid"])
        circle.refresh_from_db()
        self.assertEqual(circle.state, "ACTIVE")
        citation = f"{binding}@{document.first.pk}"
        self.assertIn(f"|min:{citation}|max:{citation}|proof:", circle.head_event.evidence_reference)
        reason = "A Board became active; voting stays on while a Board is active"
        for motion in off_motions:
            with self.subTest(thread=motion["thread_uuid"]):
                self.assertEqual(cq.phase(**motion)["phase"], "CLOSED")
                status = cq_reads.vote_status(**motion)
                self.assertEqual((status["phase"], status["result"]["markdown"]), ("CLOSED", reason))
                receipt = self.facts(status["result_uuid"])
                self.assertEqual(receipt["authority_source"], {
                    "kind": "RULE", "route": "GOVERNANCE_ACTIVATION", "reference": str(request["request_uuid"]),
                })
                self.assertEqual(receipt["facts"]["count"], {"kind": "NO_DECISION_BOARD_ACTIVATED", "cqt_applied": False})
                self.assertTrue(models.LivingOrganismEvent.objects.filter(event_uuid=receipt["act_uuid"]).exists())
        self.assertEqual(cq_reads.vote_status(**voting, ballot_uuid=ballot)["phase"], "CLOSED")
        self.assertEqual(cq.phase(**other_motion)["phase"], "PREPARATION")
        with self.exact_refusal(cq.CQRefused, "No CQ phase transition is due."):
            cq._command(**voting, request_uuid=uuid4(), operation="SETTLE", ballot=ballot)
        cq.catch_up(credential=founder.credential)
        self.assertEqual(law.query_one("""SELECT count(*) FROM public.core_cqact
            WHERE organism_id=%s AND operation='CLOSE' AND request->>'activation'=%s""",
            [lo.pk, str(request["request_uuid"])]), 3)
        self.assertEqual(law.query_one("""SELECT count(*) FROM public.core_cqact
            WHERE thread_uuid=%s AND operation='SETTLE'""", [voting["thread_uuid"]]), 0)

        snapshot = models.LOBirthingSnapshot.objects.get(living_organism=lo)
        self.assertIn(
            "birth:" + str(snapshot.snapshot_uuid), circle.head_event.evidence_reference
        )
        self.assertEqual(
            models.CircleStateEvent.objects.filter(
                circle=circle, action="ACTIVATE"
            ).count(),
            1,
        )

        # Admission of a gateway change must own its transaction. The second LO
        # needs only its founding seat to discriminate the superseded-Birthing refusal.
        other, governor = law.ordinary_birth(offering_choice="FREE", board_seats=1)
        from src.intevia.services.cq_voting_state import voting_enabled
        cut = support.database_now()
        self.assertEqual((voting_enabled("default", lo.pk, cut, cut),
                          voting_enabled("default", other.pk, cut, cut)), (True, False))
        other_circle = models.Circle.objects.get(
            parent_organism=other, kind="GOVERNANCE"
        )
        board_support.turn_off_birth_choice(other, governor)
        with self.exact_refusal(
            circles.CircleAccessRefused,
            "GOVERNANCE needs its Birthing choice or whole-membership decision and operative voting.",
        ):
            circles.activate(
                credential=governor.credential,
                organism_uuid=other.organism_id,
                circle_uuid=other_circle.circle_uuid,
                request_uuid=uuid4(),
            )

    def test_active_board_keeps_voting_on_and_records_the_refusal(self):
        """C1-108-V. Active GOVERNANCE refuses Off and records the attempt without a motion or a settings change."""
        lo, scope, people, board = self.ordinary(3, board=True)
        actor = people[0]
        at = support.database_now()
        self.assertFalse(cq_voting_state.voting_enabled("default", lo.pk, at, at))
        for effective, known in ((None, at), (at, None)):
            with self.subTest(effective=effective, known=known):
                with self.exact_refusal(
                    cq.CQRefused,
                    "Voting authority could not be checked because the effective or knowledge time was missing. "
                    "Nothing changed. Retry with both times. If the service supplied them, ask the service "
                    "administrator to check the request.",
                ):
                    cq_voting_state.voting_enabled("default", lo.pk, effective, known)
                with self.assertRaises(IntegrityError) as denied, transaction.atomic():
                    law.query_one(
                        "SELECT public.cq_settings_values_at(%s,%s,%s)",
                        [lo.pk, effective, known],
                    )
                self.assertEqual(
                    denied.exception.__cause__.diag.message_primary, "CQ_SETTINGS_TIME"
                )

        version = self.configure(scope, {"enabled": True, "clarity_stage": False})
        circles.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())
        patch = {"enabled": False}
        proposal = cq.settings_attachment(
            organism_uuid=lo.organism_id, predecessor=version, changes=patch
        )
        journey, _, topic = self.topic(scope, markdown=proposal["text"])
        opening, personal, raw = uuid4(), uuid4(), uuid4()

        def counts():
            return disc._rows(
                """SELECT count(*) FILTER (WHERE operation='SETTINGS') AS versions,
                count(*) FILTER (WHERE operation='INITIATE') AS motions FROM public.core_cqact
                WHERE organism_id=%s""",
                [lo.pk],
            )[0]

        before = counts()
        for _ in range(2):
            with self.exact_refusal(cq.CQRefused, BOARD_VOTING_MESSAGE):
                cq.put_forward(
                    **journey,
                    version_uuid=topic["version"],
                    request_uuid=opening,
                    attachment=proposal,
                )
        personal_message = "The members decide these settings by vote. Put the change forward as a Suggestion."
        with self.exact_refusal(cq.CQRefused, personal_message):
            cq.change_settings(
                **scope, predecessor=version, changes=patch, request_uuid=personal
            )

        # Bypass both service admission and cq_execute. No SETTINGS row is inserted.
        rows = disc._rows(
            """INSERT INTO public.core_cqact(act_uuid,organism_id,operation,actor_id,
             actor_epoch,received_at,request) VALUES (%s,%s,'SETTINGS',%s,%s,statement_timestamp(),
             jsonb_build_object('predecessor',%s::uuid,'changes','{"enabled":false}'::jsonb))
             RETURNING operation""",
            [raw, lo.pk, actor.pk, actor.access_epoch, version],
        )
        self.assertEqual(rows, [])
        self.assertEqual(counts(), before)
        self.assertEqual(cq.settings(**scope)["version"], version)
        self.assertTrue(cq.settings(**scope)["settings"]["enabled"])
        for key, op, code, message in (
            (opening, "INITIATE", "CQ_BOARD_ACTIVE", BOARD_VOTING_MESSAGE),
            (personal, "SETTINGS", "CQ_SETTINGS_VOTE", personal_message),
            (raw, "SETTINGS", "CQ_SETTINGS_VOTE", personal_message),
        ):
            with self.subTest(act=key):
                (receipt,) = disc._rows(
                    """SELECT a.operation,a.request->>'operation' AS attempted,
                    a.facts,a.actor_id,e.enterer_identity_id,e.action,e.occurred_at,e.received_at,
                    e.effective_at,e.recorded_at,a.fingerprint=e.payload_fingerprint AS bound
                    FROM public.core_cqact a JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
                    AND e.living_organism_id=a.organism_id WHERE a.organism_id=%s AND a.act_uuid=%s""",
                    [lo.pk, key],
                )
                self.assertEqual(
                    (receipt["operation"], receipt["attempted"], receipt["facts"]),
                    (
                        "REFUSE_VOTING_OFF",
                        op,
                        {"code": code, "rule": "CQ_BOARD_ACTIVE", "reason": message},
                    ),
                )
                self.assertEqual(
                    (
                        receipt["actor_id"],
                        receipt["enterer_identity_id"],
                        receipt["action"],
                        receipt["bound"],
                    ),
                    (actor.pk, actor.pk, "RECORD_CQ", True),
                )
                self.assertEqual(receipt["occurred_at"], receipt["received_at"])
                self.assertLessEqual(receipt["received_at"], receipt["effective_at"])
                self.assertEqual(receipt["effective_at"], receipt["recorded_at"])

    def test_defeated_attachment_has_no_effect(self):
        """DISC3-02. Mutation target is the first no-binding assertion after a real defeated vote."""
        lo, scope, people, _ = self.ordinary(3)
        self.configure(scope, {"enabled": True, "abstention": True})
        document = law.Document(people[0])
        attachment = self.attachment(lo, document)
        journey, ballot = self.ballot(scope, attachment, people)
        with self.exact_refusal(
            cq.CQRefused, "This Suggestion already has a CQ journey."
        ):
            cq.put_forward(
                **journey,
                request_uuid=uuid4(),
                version_uuid=UUID(self.facts(ballot)["facts"]["version"]),
                attachment=attachment | {"text": "Replacement text"},
            )
        for person, choice in zip(
            people, ("QUALIFIED", "NOT_QUALIFIED", "NOT_QUALIFIED")
        ):
            self.cast(journey, ballot, person, choice)
        result = self.closed(journey, ballot)
        self.assertEqual(result["count"]["kind"], "NOT_QUALIFIED_VOTE")
        self.assertEqual(
            self.binding_count(attachment),
            0,
            "A defeated vote must leave the attached law unadopted.",
        )
        self.assertEqual(result["disposition"], "NO_AUTHORITY")
        # Three is the minimum possible tie with a quorate explicit abstention.
        tied, tie = self.ballot(scope, attachment, people)
        for person, choice in zip(people, ("QUALIFIED", "NOT_QUALIFIED", "ABSTAIN")):
            self.cast(tied, tie, person, choice)
        self.assertEqual(self.closed(tied, tie)["count"]["kind"], "NOT_QUALIFIED_TIE")
        self.assertEqual(self.binding_count(attachment), 0)
        quiet, version, _ = self.motion(scope, attachment)
        self.ready(quiet, version, people)
        with clarity.due_at_phase_start():
            expired = cq.finish_clarity(**quiet, request_uuid=uuid4())
        cq.catch_up(credential=people[0].credential)
        self.assertEqual(
            self.closed(quiet, expired)["count"]["kind"],
            "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION",
        )
        self.assertEqual(self.binding_count(attachment), 0)
        with (
            self.assertRaisesRegex(IntegrityError, "CQ_COMPLETE"),
            transaction.atomic(),
            connection.cursor() as cur,
        ):
            cur.execute(
                """INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,
              actor_id,actor_epoch,received_at,request) SELECT %s,organism_id,board_uuid,thread_uuid,'IMPLEMENT',
              actor_id,actor_epoch,statement_timestamp(),jsonb_build_object('result',act_uuid)
              FROM public.core_cqact WHERE act_uuid=%s""",
                [uuid4(), result["post_uuid"]],
            )
        self.assertEqual(self.binding_count(attachment), 0)

    def test_membership_protected_act_once_and_stale_disposition(self):
        """DISC3-03. The membership adopts an Article. A competing approved target stays unapplied."""
        lo, scope, people, _ = self.ordinary(3)
        self.configure(scope, {"enabled": True, "clarity_stage": False})
        document = law.Document(people[0])
        attached = self.attachment(lo, document)
        stale = self.attachment(lo, document)
        draft, _, topic = self.topic(scope)
        wrong_body = self.attachment(lo, document, "POLICY")
        with self.exact_refusal(
            cq.CQRefused,
            "This board cannot decide this act. Put it forward in the body that owns it.",
        ):
            cq.put_forward(
                **draft,
                request_uuid=uuid4(),
                version_uuid=topic["version"],
                attachment=wrong_body,
            )
        with self.exact_refusal(
            disc.DiscussionUnavailable, "This discussion is unavailable to you."
        ):
            cq.put_forward(
                **draft,
                request_uuid=uuid4(),
                version_uuid=topic["version"],
                attachment={
                    **attached,
                    "spec": {**attached["spec"], "organism": str(uuid4())},
                },
            )
        with self.exact_refusal(
            cq.CQRefused,
            "This board cannot carry out this exact proposal. Check the body, version and current law.",
        ):
            cq.put_forward(
                **draft,
                request_uuid=uuid4(),
                version_uuid=topic["version"],
                attachment={
                    **attached,
                    "text": "Different wording from this instrument",
                },
            )
        # Both proposals are valid at putting forward; both votes open before either result.
        journey, ballot = self.ballot(scope, attached, people)
        competing, other = self.ballot(scope, stale, people)
        for member in people[:-1]:
            self.cast(journey, ballot, member)
        last_key = uuid4()
        original = self.cast(journey, ballot, people[-1], key=last_key)
        self.assertEqual(self.cast(journey, ballot, people[-1], key=last_key), original)
        result = self.closed(journey, ballot)
        self.assertEqual(
            (result["count"]["kind"], result["disposition"]), ("QUALIFIED", "APPLIED")
        )
        self.assertEqual(self.binding_count(attached), 1)
        rows = disc._rows(
            """SELECT i.authority_source,e.enterer_identity_id,i.act_uuid,a.act_uuid AS result
             FROM public.core_cqact a JOIN public.core_cqact i ON i.request->>'result'=a.act_uuid::text
             JOIN public.core_livingorganismevent e ON e.event_uuid=i.act_uuid
             WHERE a.act_uuid=%s AND i.operation='IMPLEMENT'""",
            [result["post_uuid"]],
        )
        self.assertEqual(len(rows), 1)
        with (
            self.assertRaisesRegex(IntegrityError, "CQ_COMPLETE"),
            transaction.atomic(),
            connection.cursor() as cur,
        ):
            cur.execute(
                """INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,
              actor_id,actor_epoch,received_at,request) SELECT %s,organism_id,board_uuid,thread_uuid,'IMPLEMENT',
              actor_id,actor_epoch,statement_timestamp(),jsonb_build_object('result',act_uuid,'version',2)
              FROM public.core_cqact WHERE act_uuid=%s""",
                [uuid4(), result["post_uuid"]],
            )
        self.assertEqual(self.binding_count(attached), 1)
        act = rows[0]
        self.assertNotEqual(act["act_uuid"], act["result"])
        self.assertEqual(
            (act["authority_source"], act["enterer_identity_id"]),
            (
                {
                    "kind": "CQ_RESULT",
                    "route": "CQ_MEMBERSHIP",
                    "reference": str(result["post_uuid"]),
                },
                people[-1].pk,
            ),
        )
        blocked = self.qualification(competing, other, people)
        self.assertEqual(
            (blocked["disposition"], blocked["effect_reason"]),
            (
                "NO_EFFECT",
                "The named predecessor differs from this instrument’s latest binding. Review the current version.",
            ),
        )
        self.assertEqual(self.binding_count(stale), 0)
        self.assertEqual(self.binding_count(attached), 1)
        outsider = support.register_test_learner(
            username="disc3-outsider-" + uuid4().hex
        )
        with self.exact_refusal(
            disc.DiscussionUnavailable, "This discussion is unavailable to you."
        ):
            cq_reads.result_post(
                **(journey | {"credential": outsider.credential}),
                result_uuid=result["post_uuid"],
            )

    def test_scene_five_safeguarding_policy_and_board_boundary(self):
        """DISC3-04. Five seated trustees vote 4–1 and a member follows the adoption of Policy v2."""
        lo, scope, people, circle = self.ordinary(5, board=True)
        founder, officer = people[:2]
        viewer = law.add_member(lo)
        name = "safeguarding-" + uuid4().hex
        policy_args = dict(
            **scope,
            resource_reference=name,
            authors=[founder.identity_id],
            declare_own_authorship=True,
        )
        first = library_policy.draft_policy(
            **policy_args, content="[#1] Safeguarding Policy v1.", request_uuid=uuid4()
        )
        settings = self.configure(scope, {"enabled": True})
        library_policy.publish_policy(
            **scope, version_id=first.pk, request_uuid=uuid4()
        )
        prior = law_acts.LawProposal(
            organism=lo.organism_id,
            resource=name,
            proposal=uuid4(),
            operation="ADOPT",
            intent="ADOPT",
            law_class="POLICY",
            version=1,
        )
        old = law_acts.OrganismLawService().adopt_law(law.command(founder, prior))
        self.assertEqual((old.status, old.route), ("ADOPT", "COORDINATOR_ROUTE"))
        second = library_policy.draft_policy(
            **policy_args,
            content="[#1] Safeguarding Policy v2.",
            predecessor_version_id=first.pk,
            request_uuid=uuid4(),
        )
        board = scope | {"circle_uuid": circle.circle_uuid}
        circles.activate(**board, request_uuid=uuid4())
        board_support.appoint_board_coordinator(lo, circle, founder, officer)
        for actor, route in (
            (founder, "COORDINATOR_ROUTE"),
            (officer, "GOVERNANCE_INTERIM"),
        ):
            with self.exact_refusal(
                library_policy.PolicyRefused,
                "The active GOVERNANCE Circle holds Policy. Put this act to the Board.",
            ):
                library_policy.publish_policy(
                    **(scope | {"credential": actor.credential}),
                    version_id=second.pk,
                    route=route,
                    request_uuid=uuid4(),
                )
        publication = {
            "kind": "PUBLISH_POLICY",
            "spec": {"resource": name, "organism": str(lo.organism_id), "version": 2},
            "text": second.content,
        }
        # Publication and adoption are separate votes, on the exact same text.
        disc.ensure_board(**board, request_uuid=uuid4())
        category_id = next(c["category_uuid"] for c in disc.board_categories(**board)["categories"]
                           if c["functional_code"] == "DISCUSSION")
        publish, ballot = self.ballot(
            board,
            publication,
            people,
            officer=officer,
            category_id=category_id,
            title="Publish Safeguarding Policy v2",
        )
        pub_result = self.qualification(publish, ballot, people)
        self.assertFalse(law.query_one("""SELECT public.c108_stand_down_authorised(
            %s,%s,statement_timestamp(),statement_timestamp())""", [pub_result["post_uuid"], circle.pk]))
        self.assertEqual(pub_result["disposition"], "APPLIED")
        published = cq_reads.implementation_record(
            **publish, result_uuid=pub_result["post_uuid"]
        )["effect"]
        for field, operation in (
            ("release_permission", "RELEASE"),
            ("publication", "PUBLICATION"),
        ):
            evidence = library_policy.policy_act_record(
                **(scope | {"credential": viewer.credential}), act_uuid=published[field]
            )
            self.assertEqual(
                (evidence["operation"], evidence["authority_source"]["result_post"]),
                (operation, str(pub_result["post_uuid"])),
            )
        next_policy = replace(
            prior, version=2, proposal=uuid4(), predecessor=prior.proposal
        )
        adoption = {
            "kind": "LAW",
            "spec": next_policy.content(),
            "text": second.content,
        }
        self.assertEqual(self.binding_count(adoption), 0)
        live, board_ballot = self.ballot(
            board,
            adoption,
            people,
            officer=officer,
            category_id=category_id,
            title="Adopt Safeguarding Policy v2",
        )
        for index, trustee in enumerate(people):
            self.cast(
                live,
                board_ballot,
                trustee,
                "QUALIFIED" if index < 4 else "NOT_QUALIFIED",
            )
        result = self.closed(live, board_ballot)
        self.assertEqual(
            (
                result["count"]["qualified"],
                result["count"]["not_qualified"],
                result["disposition"],
            ),
            (4, 1, "APPLIED"),
        )
        reader = scope | {"credential": viewer.credential}
        entry = next(
            r
            for r in library_laws.our_laws(**reader)["laws"]
            if r["binding"] == next_policy.proposal
        )
        source = entry["authority_source"]
        self.assertEqual(
            (entry["content"], entry["in_force_from"], entry["decision_route"]),
            (
                second.content,
                self.facts(result["post_uuid"])["effective_at"],
                "CQ_GOVERNANCE_CIRCLE",
            ),
        )
        self.assertEqual(
            (source["reference"], source["counting_rule"]["threshold"]),
            (str(result["post_uuid"]), "MAJORITY"),
        )
        self.assertEqual(
            (entry["owner"], source["circle"]), ("GOVERNANCE", str(circle.circle_uuid))
        )
        traced = cq_reads.result_post(
            **reader,
            circle_uuid=UUID(source["circle"]),
            thread_uuid=UUID(source["thread"]),
            result_uuid=UUID(source["result_post"]),
        )
        self.assertEqual(traced["count"], result["count"])
        topic = posts.read_thread(**live)["topic"]
        first_post = disc._rows(
            "SELECT post_uuid FROM public.discp_posts WHERE thread_uuid=%s AND post_number=1",
            [live["thread_uuid"]],
        )[0]["post_uuid"]
        posts.set_post_hidden(
            **(live | {"credential": officer.credential}),
            category_uuid=topic["category_uuid"],
            post_uuid=first_post,
            request_uuid=uuid4(),
            as_coordinator=True,
            hidden=True,
        )
        restricted = next(
            r
            for r in library_laws.our_laws(**reader)["laws"]
            if r["binding"] == next_policy.proposal
        )
        self.assertEqual(
            (restricted["content"], restricted["authority_source"]),
            (second.content, {"kind": "CQ_RESULT", "route": "CQ_GOVERNANCE_CIRCLE"}),
        )

        with self.exact_refusal(cq.CQRefused, BOARD_VOTING_MESSAGE):
            motions.propose(self, scope, {"enabled": False}, settings)
        retirement = replace(
            next_policy,
            proposal=uuid4(),
            predecessor=next_policy.proposal,
            operation="RETIRE",
            intent="RETIRE",
            requested_route="GOVERNANCE_INTERIM",
        )
        held = law_acts.OrganismLawService().adopt_law(law.command(officer, retirement))
        self.assertEqual(
            (held.status, held.message),
            (
                "REFUSED",
                "This Policy was adopted by Board vote. A change requires another Board vote.",
            ),
        )
        self.assertEqual(self.binding_count(adoption), 1)

    def gi_cycle(self, scope, people, citation, route):
        """Witness three separate proposals on the same GI, each with a real result."""
        key = uuid4()
        reader = {
            "organism_uuid": scope["organism_uuid"],
            "credential": people[0].credential,
        }
        cited = [{"binding": str(citation["binding"]), "version": citation["version"]}]
        version = None
        results = []
        category_id = None
        for op, text in (
            ("CREATE", "Welcome each family."),
            ("REVISE", "Welcome every family."),
            ("RETIRE", "Welcome every family."),
        ):
            spec = {
                "organism": str(scope["organism_uuid"]),
                "gi": str(key),
                "circle": str(scope["circle_uuid"])
                if scope.get("circle_uuid")
                else None,
                "operation": op,
                "predecessor": str(version) if version else None,
                "title": "Family welcome" if op != "RETIRE" else None,
                "citations": cited if op != "RETIRE" else [],
            }
            att = {"kind": "GI", "spec": spec, "text": text}
            suggestion = None
            if op == "CREATE":
                suggestion = self.topic(scope, title="Decide the family welcome intention",
                                        markdown="Decide the family welcome intention")
                probe, category_id, topic = suggestion
                with self.exact_refusal(
                    cq.CQRefused,
                    "A GI needs its exact applicable local law in force. Review its citations.",
                ):
                    cq.put_forward(
                        **probe,
                        version_uuid=topic["version"],
                        request_uuid=uuid4(),
                        attachment=att | {"spec": spec | {"citations": []}},
                    )
            journey, ballot = self.ballot(
                scope, att, people, title="Decide the family welcome intention",
                suggestion=suggestion, category_id=category_id
            )
            result = self.qualification(journey, ballot, people)
            self.assertEqual(result["disposition"], "APPLIED")
            effect = cq_reads.implementation_record(
                **journey, result_uuid=result["post_uuid"]
            )["effect"]
            history = gi.intention_history(**reader, gi_uuid=key)
            entry = history["history"][-1]
            source = entry["authority_source"]
            self.assertEqual(
                (
                    source["kind"],
                    source["route"],
                    source["result_post"],
                    source["counting_rule"]["threshold"],
                ),
                ("CQ_RESULT", route, str(result["post_uuid"]), "MAJORITY"),
            )
            self.assertEqual(
                (
                    effect["domain"],
                    effect["gi_act"],
                    effect["gi_version"],
                    entry["operation"],
                    entry["content"],
                ),
                ("GI", entry["act_uuid"], entry["version_uuid"], op, text),
            )
            self.assertEqual(effect["authority_source"], source)
            self.assertNotEqual(entry["act_uuid"], effect["implementation"])
            self.assertEqual(entry["citations"], cited)
            version = entry["version_uuid"]
            results.append(str(result["post_uuid"]))
        self.assertIsNone(history["current"])
        objective = next(
            o
            for o in gi.our_objectives(**reader, include_history=True)["objectives"]
            if o["gi"] == key
        )
        self.assertTrue(objective["retired"])
        self.assertEqual(
            [r["authority_source"]["reference"] for r in objective["history"]], results
        )
        return journey, key, category_id

    def test_lo_gi_lifecycle_and_interim_authority(self):
        """DISC3-05. Members create, revise and retire. The prior office act retains its source."""
        lo, scope, people, _ = self.ordinary(3)
        citation, adopted = gi_fixture.local_law(lo, people[0])
        args = gi_fixture.arguments(lo, people[0], citations=[citation])
        prior = gi.create_intention(**args)
        previous = gi.intention_history(**scope, gi_uuid=prior["gi"])["current"][
            "authority_source"
        ]
        self.configure(scope, {"enabled": True, "clarity_stage": False})
        self.assertEqual(
            gi.intention_history(**scope, gi_uuid=prior["gi"])["current"][
                "authority_source"
            ],
            previous,
        )
        with self.exact_refusal(
            gi.GIRefused,
            "CQ is active. Put this Guiding Intention to the LO membership.",
        ):
            gi.create_intention(
                **(args | {"gi_uuid": uuid4(), "request_uuid": uuid4()})
            )
        journey, key, category_id = self.gi_cycle(scope, people, citation, "CQ_MEMBERSHIP")
        (first,) = disc._rows(
            "SELECT category_uuid,post_uuid FROM public.discp_threads JOIN public.discp_posts USING(thread_uuid) WHERE thread_uuid=%s AND post_number=1",
            [journey["thread_uuid"]],
        )
        posts.set_post_hidden(
            **journey, **first, request_uuid=uuid4(), as_coordinator=True, hidden=True
        )
        view = gi.intention_history(
            **(scope | {"credential": people[2].credential}), gi_uuid=key
        )
        self.assertEqual(
            view["history"][-1]["authority_source"],
            {"kind": "CQ_RESULT", "route": "CQ_MEMBERSHIP"},
        )
        self.assertEqual(
            view["history"][0]["authority_source"]["counting_rule"]["threshold"],
            "MAJORITY",
        )

        # The law can cease after opening. Keep the approving vote and explain the absent GI.
        stranded = uuid4()
        plan = {
            "kind": "GI",
            "text": "A proposed objective.",
            "spec": {
                "gi": str(stranded),
                "circle": None,
                "organism": str(lo.organism_id),
                "operation": "CREATE",
                "title": "New objective",
                "predecessor": None,
                "citations": [
                    {
                        "binding": str(citation["binding"]),
                        "version": citation["version"],
                    }
                ],
            },
        }
        awaiting, ballot = self.ballot(scope, plan, people, category_id=category_id)
        retiring = replace(
            adopted,
            proposal=uuid4(),
            predecessor=adopted.proposal,
            operation="RETIRE",
            intent="RETIRE",
        )
        self.assertEqual(
            law_acts.OrganismLawService()
            .adopt_law(law.command(people[0], retiring))
            .status,
            "RETIRE",
        )
        stale = self.qualification(awaiting, ballot, people)
        self.assertEqual(
            (stale["disposition"], stale["effect_reason"]),
            (
                "NO_EFFECT",
                "A GI needs its exact applicable local law in force. Review its citations.",
            ),
        )
        self.assertEqual(
            law.query_one(
                "SELECT count(*) FROM public.core_guidingintention WHERE gi_uuid=%s",
                [stranded],
            ),
            0,
        )

    def test_circle_gi_and_governance_birth_only(self):
        """DISC3-06. An active Board keeps voting on. A Circle votes on its own GI."""
        lo, scope, people, circle = self.ordinary(3, board=True)
        founder, officer = people[:2]
        citation, _ = gi_fixture.local_law(lo, founder)
        enabled = self.configure(scope, {"enabled": True, "clarity_stage": False})
        board = scope | {"circle_uuid": circle.circle_uuid}
        circles.activate(**board, request_uuid=uuid4())
        board_support.appoint_board_coordinator(lo, circle, founder, officer)
        board["credential"] = officer.credential
        with self.exact_refusal(cq.CQRefused, BOARD_VOTING_MESSAGE):
            motions.propose(self, scope, {"enabled": False}, enabled)
        args = gi_fixture.arguments(lo, officer, citations=[citation])
        args["route"] = "GOVERNANCE_INTERIM"
        with self.exact_refusal(
            gi.GIRefused,
            "CQ is active. Put this Guiding Intention to the LO membership.",
        ):
            gi.create_intention(
                **(args | {"gi_uuid": uuid4(), "request_uuid": uuid4()})
            )
        with self.exact_refusal(
            gi.GIRefused, "CQ is active. Put this Guiding Intention to this Circle."
        ):
            gi.create_intention(
                **gi_fixture.arguments(lo, officer, circle=circle, citations=[citation])
            )
        self.gi_cycle(board, people, citation, "CQ_CIRCLE")

    def test_below_three_referral_and_office_limits(self):
        """DISC3-07. A new body starts afresh. Recusals and the boundary on personal authority follow the matter."""
        lo, scope, people, circle = self.ordinary(3, board=True)
        fourth = law.add_member(lo)
        founder, officer, recused = people
        self.configure(scope, {"enabled": True})
        board = scope | {"circle_uuid": circle.circle_uuid}
        circles.activate(**board, request_uuid=uuid4())
        board_support.appoint_board_coordinator(lo, circle, founder, officer)
        board["credential"] = officer.credential
        original, board_category, topic = self.topic(board)
        cq.put_forward(**original, version_uuid=topic["version"], request_uuid=uuid4())
        self.ready(original, topic["version"], people)
        opened = cq.finish_clarity(**original, request_uuid=uuid4())
        self.cast(original, opened, founder)
        cq.declare_conflict(
            **(original | {"credential": recused.credential}),
            request_uuid=uuid4(),
            basis="Proposed supplier",
        )
        loss = self.closed(original, opened)
        self.assertEqual(
            (
                loss["count"]["kind"],
                loss["count"]["participated"],
                loss["referral_destination"],
            ),
            ("NO_DECISION_FEWER_THAN_THREE", 1, "MEMBERSHIP"),
        )
        origin = loss["post_uuid"]
        next_motion, lo_category, next_topic = self.topic(scope)
        new = cq.put_forward(
            **next_motion,
            request_uuid=uuid4(),
            version_uuid=next_topic["version"],
            referral_result=origin,
        )
        self.assertEqual(
            cq.phase(**next_motion)["referred_from"]["result_uuid"], str(origin)
        )
        self.assertEqual(loss["referral"]["wording_version"], str(topic["version"]))
        joined = self.facts(new)["facts"]
        self.assertEqual(joined["referral_result"], str(origin))
        self.assertEqual(
            joined["wording_fingerprint"],
            self.facts(origin)["facts"]["wording_fingerprint"],
        )
        self.assertNotEqual(next_topic["version"], topic["version"])
        other, _, copy = self.topic(scope, category_id=lo_category)
        with self.exact_refusal(
            cq.CQRefused, "This referral already has a receiving CQ journey."
        ):
            cq.put_forward(
                **other,
                request_uuid=uuid4(),
                version_uuid=copy["version"],
                referral_result=origin,
            )
        self.ready(next_motion, next_topic["version"], [founder, officer, fourth])
        with self.exact_refusal(
            cq.CQRefused,
            "Only an eligible member of this board may confirm clarity for this CQ.",
        ):
            cq.confirm_clarity(
                **(next_motion | {"credential": recused.credential}),
                request_uuid=uuid4(),
                version_uuid=next_topic["version"],
            )
        new_vote = cq.finish_clarity(**next_motion, request_uuid=uuid4())
        with self.exact_refusal(
            cq.CQRefused,
            "You declared a conflict for this proposal and cannot vote on it.",
        ):
            self.cast(next_motion, new_vote, recused)
        cq.declare_conflict(
            **(next_motion | {"credential": fourth.credential}),
            request_uuid=uuid4(),
            basis="New conflict",
        )
        exhausted = self.closed(next_motion, new_vote)
        self.assertEqual(
            (exhausted["count"]["eligible"], exhausted["referral_destination"]),
            (2, "NONE"),
        )
        self.assertIsNone(
            cq_reads.implementation_record(
                **next_motion, result_uuid=exhausted["post_uuid"]
            )["effect"]
        )

        document = law.Document(founder)
        article = law.adoption(lo, document, law_class="ARTICLE")
        frozen = {
            "kind": "LAW",
            "spec": article.content(),
            "text": document.first.content,
        }
        protected, vote = self.ballot(scope, frozen, [*people, fourth], category_id=lo_category)
        for absent in (recused, fourth):
            cq.declare_conflict(
                **(protected | {"credential": absent.credential}),
                request_uuid=uuid4(),
                basis="Interest in the Article",
            )
        terminal = self.closed(protected, vote)
        self.assertEqual(
            (terminal["count"]["kind"], terminal["referral_destination"]),
            ("NO_DECISION_FEWER_THAN_THREE", "NONE"),
        )
        self.assertEqual(self.binding_count(frozen), 0)
        held = law_acts.OrganismLawService().adopt_law(
            law.command(
                founder, replace(article, referral_result=terminal["post_uuid"])
            )
        )
        self.assertEqual(
            (held.status, held.message),
            (
                "REFUSED",
                "This referral needs its competent body or its sole unrecused LO Coordinator.",
            ),
        )
        # An unprotected Procedure can reach the sole LO Coordinator, but the
        # Board's loss itself adopts nothing. A recused Coordinator cannot act.
        for member, destination in ((recused, "LO_COORDINATOR"), (founder, "NONE")):
            doc = law.Document(founder)
            proposal = law.adoption(lo, doc, law_class="PROCEDURE")
            attachment = {
                "kind": "LAW",
                "spec": proposal.content(),
                "text": doc.first.content,
            }
            journey, vote = self.ballot(board, attachment, people, category_id=board_category)
            cq.declare_conflict(
                **(journey | {"credential": member.credential}),
                request_uuid=uuid4(),
                basis="Interest in the procedure",
            )
            refused = self.closed(journey, vote)
            self.assertEqual(refused["referral_destination"], destination)
            self.assertEqual(self.binding_count(attachment), 0)
            request = law.command(
                founder, replace(proposal, referral_result=refused["post_uuid"])
            )
            personal = law_acts.OrganismLawService().adopt_law(request)
            if destination == "NONE":
                self.assertEqual(
                    (personal.status, personal.message),
                    (
                        "REFUSED",
                        "This referral needs its competent body or its sole unrecused LO Coordinator.",
                    ),
                )
                omitted = law_acts.OrganismLawService().adopt_law(
                    law.command(founder, proposal)
                )
                self.assertEqual(
                    (omitted.status, omitted.message),
                    (
                        "REFUSED",
                        "This proposal cannot be used as a separate personal act. Follow its recorded referral.",
                    ),
                )
                self.assertEqual(self.binding_count(attachment), 0)
            else:
                self.assertEqual(
                    (personal.status, personal.route), ("ADOPT", "COORDINATOR_ROUTE")
                )
                entry = next(
                    r
                    for r in library_laws.our_laws(**scope)["laws"]
                    if r["binding"] == proposal.proposal
                )
                self.assertEqual(
                    entry["authority_source"]["referral_result"],
                    str(refused["post_uuid"]),
                )
                self.assertNotEqual(entry["authority_source"]["kind"], "CQ_RESULT")

    def test_concurrent_final_answer_rollback_and_reversal(self):
        """DISC3-08. Domain failure rolls back the final answer. Two real connections replay one effect."""
        lo, scope, people, _ = self.ordinary(3)
        self.configure(scope, {"enabled": True, "clarity_stage": False})
        attachment = self.attachment(lo, law.Document(people[0]))
        journey, ballot = self.ballot(scope, attachment, people)
        for person in people[:2]:
            self.cast(journey, ballot, person)
        key = uuid4()

        def finish():
            return self.cast(journey, ballot, people[-1], key=key)

        # Fail exactly at the domain insert, after the final answer and result have been written.
        with connection.cursor() as cur:
            cur.execute("""CREATE FUNCTION public.disc3_test_binding_failure() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'DISC3 witness domain failure' USING ERRCODE='23514'; END $$""")
            try:
                cur.execute("""CREATE TRIGGER disc3_fixture_failure BEFORE INSERT ON public.core_organismlawbinding
                    FOR EACH ROW EXECUTE FUNCTION public.disc3_test_binding_failure()""")
                try:
                    with self.assertRaises(IntegrityError) as fault:
                        finish()
                    self.assertEqual(
                        fault.exception.__cause__.diag.message_primary,
                        "DISC3 witness domain failure",
                    )
                finally:
                    cur.execute(
                        "DROP TRIGGER disc3_fixture_failure ON public.core_organismlawbinding"
                    )
            finally:
                cur.execute("DROP FUNCTION public.disc3_test_binding_failure()")
        self.assertFalse(
            models.LivingOrganismEvent.objects.filter(event_uuid=key).exists()
        )
        self.assertEqual(self.binding_count(attachment), 0)
        status = cq_reads.vote_status(**journey)
        self.assertEqual(
            (status["phase"], status["participated"], status["result_uuid"]),
            ("VOTING", 2, None),
        )
        first, replay = clarity.CQClarityTests.contended_commands(self, finish, finish)
        self.assertEqual((first, replay.result(timeout=5)), (key, key))
        result = self.closed(journey, ballot)["post_uuid"]
        self.assertEqual(self.binding_count(attachment), 1)
        effect = cq_reads.implementation_record(**journey, result_uuid=result)["effect"]
        self.assertEqual(
            effect["adopted_binding"], UUID(attachment["spec"]["proposal"])
        )
        self.assertEqual(
            law.query_one(
                "SELECT count(*) FROM public.core_cqact WHERE operation='IMPLEMENT' AND request->>'result'=%s",
                [str(result)],
            ),
            1,
        )
        migration = import_module("core.migrations.0076_cq_authority")
        with (
            connection.schema_editor(atomic=True) as editor,
            self.exact_refusal(
                RuntimeError,
                "DISC3 has recorded CQ authority or Birthing activation. Preserve its history.",
            ),
        ):
            migration.reverse(None, editor)
        self.assertEqual(
            cq_reads.implementation_record(**journey, result_uuid=result)["effect"],
            effect,
        )

    test_concurrent_final_answer_rollback_and_reversal.ISOLATION_FULL_CAPTURE = True
