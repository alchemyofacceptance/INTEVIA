"""DISC4. An LO's recorded voting authority governs its settings."""

from dataclasses import replace
from importlib import import_module
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_commands, cq, cq_reads, law_acts
from src.intevia.services import discussion as disc
from src.intevia.services import discussion_posts as posts
from tests import cq_authority_support as boards
from tests import cq_settings_support as motions
from tests import s015_0021_support as support
from tests import test_cq_decisions as decisions
from tests import test_law_acts as fixtures
from tests.test_cq_clarity import elapsed_clarity, subscriber_options_lapsed
from tests.test_law_records import Document


class CQSettingsAuthorityTests(TransactionTestCase):
    owner = decisions.CQDecisionTests.owner
    topic = decisions.CQDecisionTests.topic
    configure = motions.configure
    setup_board = decisions.CQDecisionTests.setup_board
    facts = decisions.CQDecisionTests.facts
    refused = decisions.CQDecisionTests.refused
    cast = decisions.CQDecisionTests.cast
    confirmed = decisions.CQDecisionTests.confirmed

    def test_01_qualified_settings_cite_deciding_result(self):
        """DISC4-01. A qualified change cites its result in the version, ILC and permitted history."""
        _, scope, people, original = self.setup_board(3)
        journey, wording = motions.propose(self, scope, {"clarity_days": 10}, original)
        ballot = motions.open_motion(journey, wording, people)
        # An ordinary member's final answer supplies the enterer of the effect.
        result = motions.settle(journey, ballot, people)
        decision = result["post_uuid"]
        before, after = cq.settings_history(**scope)
        self.assertEqual(
            after["decision"],
            decision,
            "The settings version must cite the deciding CQ result.",
        )
        self.assertNotEqual(after["act_uuid"], decision)
        self.assertEqual(
            (before["decision"], before["authority_source"]["kind"]),
            (original, "OFFICE"),
        )
        self.assertEqual(after["facts"]["settings"]["clarity_days"], 10)
        source = after["authority_source"]
        self.assertEqual(
            (
                source["kind"],
                source["result_post"],
                source["counting_rule"]["clarity_days"],
            ),
            ("CQ_RESULT", str(decision), 7),
        )
        self.assertEqual(after["facts"]["predecessor"], str(original))
        effect = cq_reads.implementation_record(**journey, result_uuid=decision)[
            "effect"
        ]
        self.assertEqual(
            (effect["domain"], effect["settings_version"]),
            ("SETTINGS", after["act_uuid"]),
        )
        event = disc._rows(
            """SELECT action,enterer_identity_id,evidence_reference,payload_fingerprint
            FROM public.core_livingorganismevent WHERE event_uuid=%s""",
            [after["act_uuid"]],
        )[0]
        self.assertEqual(
            event,
            {
                "action": "RECORD_CQ",
                "enterer_identity_id": people[-1].pk,
                "evidence_reference": "cq-settings-result:" + str(decision),
                "payload_fingerprint": after["fingerprint"],
            },
        )
        with (
            self.assertRaises(RuntimeError) as reversal,
            transaction.atomic(),
            connection.schema_editor() as editor,
        ):
            import_module("core.migrations.0077_cq_settings_authority").reverse(
                None, editor
            )
        self.assertEqual(
            str(reversal.exception),
            "DISC4 settings authority has been recorded. Preserve its history.",
        )

    def test_02_only_qualified_current_target_takes_effect(self):
        """DISC4-02. Only a competent, current, qualified patch takes effect: wrong body, defeat, other result kinds, stale target and forgery."""
        lo, scope, people, original = self.setup_board(3)
        journey, wording = motions.propose(self, scope, {"vote_days": 9}, original)
        ballot = motions.open_motion(journey, wording, people)
        lost = motions.settle(
            journey, ballot, people, ["QUALIFIED", "NOT_QUALIFIED", "NOT_QUALIFIED"]
        )
        self.assertEqual(
            (lost["count"]["kind"], lost["disposition"]),
            ("NOT_QUALIFIED_VOTE", "NO_AUTHORITY"),
        )
        self.assertIsNone(
            cq_reads.implementation_record(**journey, result_uuid=lost["post_uuid"])[
                "effect"
            ]
        )
        self.assertEqual(cq.settings(**scope)["version"], original)

        kinds = set(cq_reads.RESULT_LABELS) | set(cq_reads.NO_BALLOT_LABELS)
        rules = disc._rows(
            "SELECT label,public.cq3_qualified(label) AS authorised FROM unnest(%s::text[]) label",
            [sorted(kinds)],
        )
        self.assertEqual({r["label"] for r in rules if r["authorised"]}, {"QUALIFIED"})
        circle = circle_commands.form(
            **scope,
            circle_uuid=uuid4(),
            kind="GENERIC",
            purpose="Review proposals",
            request_uuid=uuid4(),
        )
        self.refused(
            "CQ_BODY",
            "This board cannot decide this act. Put it forward in the body that owns it.",
            motions.propose,
            case=self,
            scope=scope | {"circle_uuid": circle.circle_uuid},
            changes={"vote_days": 10},
            predecessor=original,
        )

        old, wording = motions.propose(self, scope, {"vote_days": 10}, original)
        old_ballot = motions.open_motion(old, wording, people)
        accepted = self.configure(scope, {"vote_days": 8}, original)
        stale = motions.settle(old, old_ballot, people)
        self.assertEqual(
            (stale["count"]["kind"], stale["disposition"], stale["effect_reason"]),
            (
                "QUALIFIED",
                "NO_EFFECT",
                "The settings version changed. Put forward a proposal against the current version.",
            ),
        )
        self.assertIsNone(
            cq_reads.implementation_record(**old, result_uuid=stale["post_uuid"])[
                "effect"
            ]
        )
        with (
            self.assertRaises(IntegrityError) as forged,
            transaction.atomic(),
            connection.cursor() as cur,
        ):
            cur.execute(
                """INSERT INTO public.core_cqact(act_uuid,organism_id,operation,actor_id,actor_epoch,received_at,request)
                VALUES (%s,%s,'SETTINGS',%s,%s,statement_timestamp(),
                jsonb_build_object('result',%s::uuid,'predecessor',%s::uuid,'changes','{"vote_days":10}'::jsonb))""",
                [
                    uuid4(),
                    lo.pk,
                    people[0].pk,
                    people[0].access_epoch,
                    stale["post_uuid"],
                    accepted,
                ],
            )
        self.assertEqual(forged.exception.__cause__.diag.message_primary, "CQ_COMPLETE")
        self.assertEqual(
            [v["act_uuid"] for v in cq.settings_history(**scope)], [original, accepted]
        )

    def test_03_office_route_obeys_voting_state(self):
        """DISC4-03. Bootstrap, below three, third member, voted switch-off, restart, outsider and the exact below-three motion."""
        lo, scope, people, first = self.setup_board(2)
        small = self.configure(scope, {"vote_days": 4}, first)
        facts = self.facts(small)["facts"]
        self.assertEqual(
            (facts["basis"], facts["eligible_count"], facts["reason"]),
            (
                "HG:DISC4:Q1b",
                2,
                "Fewer than three eligible members can decide the settings by vote.",
            ),
        )
        people.append(fixtures.add_member(lo))
        self.assertEqual(
            cq.change_settings(
                **scope, changes={"vote_days": 4}, predecessor=first, request_uuid=small
            ),
            small,
        )
        args = {
            "changes": {"enabled": False},
            "predecessor": small,
            "request_uuid": uuid4(),
        }
        self.refused(
            "CQ_SETTINGS_VOTE",
            "The members decide these settings by vote. Put the change forward as a Suggestion.",
            cq.change_settings,
            **scope,
            **args,
        )
        self.refused(
            "CQ_SETTINGS_OFFICE",
            "Only this Living Organism's effective Coordinator may change its CQ settings.",
            cq.change_settings,
            **(scope | {"credential": people[-1].credential}),
            **args,
        )
        outsider = support.register_test_learner(
            username="disc4-outsider-" + uuid4().hex
        )
        with self.assertRaises(disc.DiscussionUnavailable) as denied:
            cq.change_settings(**(scope | {"credential": outsider.credential}), **args)
        self.assertEqual(
            str(denied.exception), "This discussion is unavailable to you."
        )
        off = self.configure(scope, {"enabled": False}, small)
        self.refused(
            "CQ_SETTINGS_RESTART",
            "Re-enable CQ with the recorded settings, then put the change to the members.",
            cq.change_settings,
            **scope,
            changes={"enabled": True, "vote_days": 6},
            predecessor=off,
            request_uuid=uuid4(),
        )
        restart = cq.change_settings(
            **scope, changes={"enabled": True}, predecessor=off, request_uuid=uuid4()
        )
        self.assertEqual(
            (
                self.facts(restart)["facts"]["settings"]["vote_days"],
                self.facts(restart)["authority_source"]["kind"],
            ),
            (4, "OFFICE"),
        )
        self.assertEqual(
            [v["act_uuid"] for v in cq.settings_history(**scope)],
            [first, small, off, restart],
        )

        # The whole LO still has three members. One is ineligible for this motion.
        change, wording = motions.propose(self, scope, {"vote_days": 6}, restart)
        ballot = motions.open_motion(change, wording, people)
        cq.declare_conflict(
            **(change | {"credential": people[1].credential}),
            request_uuid=uuid4(),
            basis="I am the interested party.",
        )
        below = cq_reads.vote_status(**change, ballot_uuid=ballot)["result"]
        self.assertEqual(
            (below["count"]["kind"], below["referral_destination"]),
            ("NO_DECISION_FEWER_THAN_THREE", "LO_COORDINATOR"),
        )
        self.assertEqual(cq.settings(**scope)["version"], restart)
        self.refused(
            "CQ_SETTINGS_REFERRAL",
            "Use the exact settings motion that closed below three eligible members. A Coordinator who declared a conflict cannot decide it.",
            cq.change_settings,
            **scope,
            changes={"vote_days": 9},
            predecessor=restart,
            request_uuid=uuid4(),
            below_three_result=below["post_uuid"],
        )
        act = cq.change_settings(
            **scope,
            changes={"vote_days": 6},
            predecessor=restart,
            request_uuid=uuid4(),
            below_three_result=below["post_uuid"],
        )
        saved = self.facts(act)
        self.assertEqual(
            (
                saved["authority_source"]["kind"],
                saved["facts"]["eligible_count"],
                saved["facts"]["below_three_result"],
            ),
            ("OFFICE", 2, str(below["post_uuid"])),
        )

    def test_04_open_ballot_keeps_its_rules(self):
        """DISC4-04. Both the deciding ballot and an open peer keep their opening rule; a later vote uses the new one."""
        _, scope, people, original = self.setup_board(3)
        peer, _ = self.confirmed(scope, people)
        peer_ballot = cq.finish_clarity(**peer, request_uuid=uuid4())
        change, wording = motions.propose(self, scope, {"abstention": True}, original)
        change_ballot = motions.open_motion(change, wording, people)
        decided = motions.settle(change, change_ballot, people)
        applied = cq_reads.implementation_record(
            **change, result_uuid=decided["post_uuid"]
        )["effect"]["settings_version"]
        for role, ballot in (
            ("deciding vote", change_ballot),
            ("already open", peer_ballot),
        ):
            with self.subTest(role=role):
                saved = self.facts(ballot)["facts"]
                self.assertEqual(
                    (saved["settings_version"], saved["rules"]["abstention"]),
                    (str(original), False),
                )
        self.refused(
            "CQ_ABSTAIN_OFF",
            "Abstention is not available for this vote.",
            self.cast,
            journey=peer,
            ballot=peer_ballot,
            member=people[0],
            choice="ABSTAIN",
        )
        later, _ = self.confirmed(scope, people)
        later_ballot = cq.finish_clarity(**later, request_uuid=uuid4())
        self.assertEqual(
            self.facts(later_ballot)["facts"]["settings_version"], str(applied)
        )
        key = uuid4()
        self.assertEqual(
            self.cast(later, later_ballot, people[0], choice="ABSTAIN", key=key), key
        )

    def reporting_motion(self, scope, choice, predecessor=None, *, target=None):
        proposal = cq.circle_reporting_attachment(
            organism_uuid=scope["organism_uuid"],
            circle_uuid=target or scope["circle_uuid"],
            reporting=choice,
            predecessor=predecessor,
        )
        journey, _, post = self.topic(
            scope, title="Choose Circle reporting", markdown=proposal["text"]
        )
        cq.put_forward(
            **journey,
            request_uuid=uuid4(),
            version_uuid=post["version"],
            attachment=proposal,
        )
        return journey, post["version"]

    def test_05_circle_reporting_inherits_or_uses_its_vote(self):
        """DISC4-05. Local authority, sibling inheritance, frozen ballots, and an exact Article overriding the local preference."""
        lo, founder, scope = self.owner(beta=True)

        def form(purpose):
            circle = circle_commands.form(
                **scope,
                circle_uuid=uuid4(),
                kind="GENERIC",
                purpose=purpose,
                request_uuid=uuid4(),
            )
            return scope | {"circle_uuid": circle.circle_uuid}

        local, sibling = form("Reserves"), form("Access")
        # Adopt the exact Article while the real sole-member route is available.
        doc = Document(founder)
        clause = doc.service.create_successor_version(
            identity=doc.author,
            resource_id=doc.name,
            content="[#1] A Circle designated under this Article reports named participation in its designated decisions.",
            evidence_reference="disc4:reporting-article",
        )
        doc.transition("publish_library_resource")
        article = replace(
            fixtures.adoption(lo, doc, "ARTICLE"), version=clause.version_number
        )
        enacted = law_acts.OrganismLawService().adopt_law(
            fixtures.command(founder, article)
        )
        self.assertEqual(enacted.route, "SOLE_MEMBER_COORDINATOR")
        initial = self.configure(scope, {"enabled": True})
        voters = [founder, fixtures.add_member(lo), fixtures.add_member(lo)]
        for voter in voters[1:]:
            membership = models.OrganismMembership.objects.get(
                living_organism=lo, identity=voter
            )
            request = circle_commands.request_place(
                **(local | {"credential": voter.credential}),
                membership_uuid=membership.membership_uuid,
                request_uuid=uuid4(),
                consent="Serve on the reserves Circle",
            )
            circle_commands.admit(**local, request_uuid=request.request_uuid)
        self.assertEqual(cq.settings(**local)["reporting_origin"], "PLATFORM")
        self.refused(
            "CQ_BODY",
            "This board cannot decide this act. Put it forward in the body that owns it.",
            self.reporting_motion,
            scope=scope,
            target=local["circle_uuid"],
            choice="TOTALS_ONLY",
        )
        self.refused(
            "CQ_REPORT_SCOPE",
            "Each subscribing Circle decides its own reporting. Put a local change to that Circle.",
            motions.propose,
            case=self,
            scope=scope,
            changes={"reporting_scope": "DELEGATED"},
            predecessor=initial,
        )
        change, word = self.reporting_motion(local, "TOTALS_ONLY")
        ballot = motions.open_motion(change, word, voters)
        result = motions.settle(change, ballot, voters)
        self.assertEqual(
            (result["disposition"], result["reporting_rule"]["value"]),
            ("APPLIED", "NAMED"),
        )
        effect = cq_reads.implementation_record(
            **change, result_uuid=result["post_uuid"]
        )["effect"]
        self.assertEqual(
            (effect["domain"], effect["authority_source"]["result_post"]),
            ("CIRCLE_REPORTING", str(result["post_uuid"])),
        )
        version = effect["circle_reporting_version"]
        current = cq.settings(**local)
        self.assertEqual(
            (
                current["settings"]["reporting"],
                current["reporting_origin"],
                current["local_version"],
            ),
            ("TOTALS_ONLY", "CIRCLE", version),
        )
        for other in (scope, sibling):
            self.assertEqual(cq.settings(**other)["settings"]["reporting"], "NAMED")
        self.assertEqual(cq.settings_history(**sibling), [])
        old, _ = self.confirmed(local, voters)
        old_ballot = cq.finish_clarity(**old, request_uuid=uuid4())

        citation = {
            "binding": str(article.proposal),
            "version": clause.pk,
            "provision": "1",
            "body": str(local["circle_uuid"]),
            "decision": "ALL_DECISIONS",
            "stage": "VOTE",
            "denominator": "Eligible Reserves Circle members",
            "setting": "REPORTING",
            "value": {"reporting": "NAMED"},
        }
        with subscriber_options_lapsed():
            law_version = self.configure(scope, {"provisions": [citation]}, initial)
        current = cq.settings(**local)
        self.assertEqual(
            (
                current["reporting_origin"],
                current["reporting_rule"]["provision"],
                current["settings"]["reporting"],
            ),
            ("CONSTITUTION", citation, "NAMED"),
        )
        self.assertEqual(current["local_version"], version)
        self.assertTrue(cq.settings(**sibling)["inherits_reporting"])
        saved = motions.settle(old, old_ballot, voters)
        self.assertNotIn("participants", saved)
        trace = cq_reads.reporting_decision(**old, result_uuid=saved["post_uuid"])
        self.assertEqual(
            (
                trace["version"],
                trace["decision"],
                trace["previous_value"],
                trace["value"],
            ),
            (version, result["post_uuid"], "NAMED", "TOTALS_ONLY"),
        )
        self.refused(
            "CQ_REPORTING_LAW",
            "The cited constitutional provision fixes reporting for this Circle. Follow the provision or change the law first.",
            self.reporting_motion,
            scope=local,
            choice="TOTALS_ONLY",
            predecessor=version,
        )
        later, _ = self.confirmed(local, voters)
        after = motions.settle(
            later, cq.finish_clarity(**later, request_uuid=uuid4()), voters
        )
        trace = cq_reads.reporting_decision(**later, result_uuid=after["post_uuid"])
        self.assertEqual(
            (len(after["participants"]), trace["version"], trace["provision"]),
            (3, law_version, citation),
        )
        self.assertEqual(
            trace["authority_source"]["result_post"], str(trace["decision"])
        )
        with (
            self.assertRaisesMessage(
                RuntimeError,
                "Circle reporting authority has been recorded. Preserve its history.",
            ),
            transaction.atomic(),
            connection.schema_editor() as editor,
        ):
            import_module("core.migrations.0078_cq_circle_reporting").reverse(
                None, editor
            )

    test_05_circle_reporting_inherits_or_uses_its_vote.ISOLATION_FULL_CAPTURE = True

    def test_06_reporting_trace_respects_audience_and_lapse(self):
        """DISC4-06. GOVERNANCE result audience, local choice over lapse, stale entitlement at settlement, and a voted return to inheritance."""
        lo, founder = fixtures.ordinary_birth(
            offering_choice="COMPLIMENTARY_BETA", board_seats=3
        )
        scope = {"organism_uuid": lo.organism_id, "credential": founder.credential}
        circle = models.Circle.objects.get(parent_organism=lo, kind="GOVERNANCE")
        voters = [founder] + [
            boards.seat_trustee(lo, founder, circle, n) for n in (1, 2)
        ]
        self.configure(scope, {"enabled": True, "reporting": "TOTALS_ONLY"})
        local = scope | {"circle_uuid": circle.circle_uuid}
        circle_commands.activate(**local, request_uuid=uuid4())
        boards.appoint_board_coordinator(lo, circle, founder, voters[1])
        local["credential"] = voters[1].credential
        viewer = fixtures.add_member(lo)
        unseated = local | {"credential": viewer.credential}
        change, wording = self.reporting_motion(local, "NAMED")
        choice = motions.settle(
            change, motions.open_motion(change, wording, voters), voters
        )
        version = cq.settings(**local)["local_version"]
        self.assertEqual(
            (choice["disposition"], choice["reporting_rule"]["value"]),
            ("APPLIED", "TOTALS_ONLY"),
        )
        self.assertNotIn("participants", choice)
        (version_row,) = cq.settings_history(**local)
        self.assertEqual(
            (
                version_row["version"],
                version_row["decision"],
                version_row["evidence_reference"],
            ),
            (
                version,
                choice["post_uuid"],
                "cq-settings-result:" + str(choice["post_uuid"]),
            ),
        )
        self.assertNotIn("enterer_identity_uuid", version_row)
        for read in (cq.settings, cq.settings_history):
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                read(**unseated)
        # A fresh selection is valid at initiation. Entitlement must still hold at settlement.
        competing, word = self.reporting_motion(local, "TOTALS_ONLY", version)
        open_ballot = motions.open_motion(competing, word, voters)
        with subscriber_options_lapsed():
            status = cq_reads.vote_status(
                **(competing | {"credential": viewer.credential}),
                ballot_uuid=open_ballot,
            )
            self.assertEqual(
                (
                    status["reporting_rule"]["version"],
                    status["reporting_rule"]["value"],
                ),
                (str(version), "NAMED"),
            )
            held = motions.settle(competing, open_ballot, voters)
            self.assertEqual(
                (held["count"]["kind"], held["disposition"], held["effect_reason"]),
                ("QUALIFIED", "NO_EFFECT", cq.MESSAGES["CQ_SUBSCRIBER"]),
            )
            self.assertEqual(cq.settings(**local)["local_version"], version)
            visible = competing | {"credential": viewer.credential}
            exact = {"result_uuid": held["post_uuid"]}
            trace = cq_reads.reporting_decision(**visible, **exact)
            self.assertEqual(
                (
                    trace["origin"],
                    trace["version"],
                    trace["authority_source"]["result_post"],
                ),
                ("CIRCLE", version, str(choice["post_uuid"])),
            )
            self.assertNotIn("enterer_identity_uuid", trace)
            outsider = support.register_test_learner(
                username="disc4-external-" + uuid4().hex
            )
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                cq_reads.reporting_decision(
                    **(competing | {"credential": outsider.credential}), **exact
                )
            self.refused(
                "CQ_SUBSCRIBER",
                "Subscriber access is required to select this CQ option. An option already chosen stays in force.",
                self.reporting_motion,
                scope=local,
                choice="TOTALS_ONLY",
                predecessor=version,
            )
            reset, word = self.reporting_motion(local, "INHERIT", version)
            inherited = motions.settle(
                reset, motions.open_motion(reset, word, voters), voters
            )
            self.assertEqual(inherited["disposition"], "APPLIED")
            current = cq.settings(**local)
            self.assertEqual(
                (
                    current["inherits_reporting"],
                    current["reporting_origin"],
                    current["settings"]["reporting"],
                ),
                (True, "LO", "TOTALS_ONLY"),
            )
            first, last = cq.settings_history(**local)
            self.assertEqual(
                (last["predecessor"], last["choice"], last["decision"]),
                (str(first["version"]), "INHERIT", inherited["post_uuid"]),
            )
            # A ballot-less close uses the same lookup. Follow both decisions from this outcome.
            closed, _, post = self.topic(local)
            cq.put_forward(**closed, request_uuid=uuid4(), version_uuid=post["version"])
            window = cq.mark_prepared(**closed, request_uuid=uuid4())
            with elapsed_clarity(window, response_due=False):
                close = cq.close_without_vote(**closed, request_uuid=uuid4())
            linked = cq_reads.reporting_decision(
                **(closed | {"credential": viewer.credential}), result_uuid=close
            )
            self.assertEqual(
                (
                    linked["origin"],
                    linked["value"],
                    linked["inheritance_decision"]["decision"],
                ),
                ("LO", "TOTALS_ONLY", inherited["post_uuid"]),
            )
            self.assertEqual(
                cq_reads.result_post(**change, result_uuid=choice["post_uuid"])[
                    "reporting_rule"
                ]["value"],
                "TOTALS_ONLY",
            )

        # The later result remains readable when the deciding thread is hidden.
        # Its reporting version remains identifiable, but its private decision does not.
        deciding_thread = posts.read_thread(**change)
        posts.set_post_hidden(
            **change,
            category_uuid=deciding_thread["topic"]["category_uuid"],
            post_uuid=deciding_thread["posts"][0]["post_uuid"],
            request_uuid=uuid4(),
            hidden=True,
            as_coordinator=True,
        )
        redacted = cq_reads.reporting_decision(**visible, **exact)
        self.assertEqual(redacted["version"], version)
        self.assertEqual(
            redacted["authority_source"], {"kind": "CQ_RESULT", "route": "CQ_CIRCLE"}
        )
        for field in (
            "decision",
            "evidence_reference",
            "actor_identity_uuid",
            "enterer_identity_uuid",
        ):
            self.assertNotIn(field, redacted)

    test_06_reporting_trace_respects_audience_and_lapse.ISOLATION_FULL_CAPTURE = True
