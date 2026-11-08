"""DISC2-01 to DISC2-08. P1b obligations, using the real command and ILC paths."""

from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from functools import partial
from importlib import import_module
from time import monotonic
from uuid import uuid4

from django.db import IntegrityError, connection, connections, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import (
    circle_commands,
    cq,
    cq_reads,
    law_acts,
    organism_membership_service,
)
from src.intevia.services import discussion as disc
from src.intevia.services import discussion_posts as posts
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import S015Authority
from tests import discussion_support
from tests import s015_0021_support as support
from tests import test_law_acts as fixtures
from tests.test_law_records import Document, query_one


@contextmanager
def due_at_phase_start():
    """A disposable calendar fixture; only the computed deadline input is set.

    All receipt, execution, recording and authority cuts use actual DB time.
    Restore the calculator before leaving this transaction, even on failure.
    """
    signature = "public.cq_deadline(timestamptz,integer)"
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("SELECT pg_get_functiondef(%s::regprocedure)", [signature])
        original = cur.fetchone()[0]
        cur.execute("""CREATE OR REPLACE FUNCTION public.cq_deadline(p_start timestamptz,p_days integer)
            RETURNS timestamptz LANGUAGE sql IMMUTABLE STRICT AS $$ SELECT p_start $$""")
        try:
            yield
        finally:
            cur.execute(original, params=None)


@contextmanager
def elapsed_clarity(window, *, response_due):
    """Present one stored window as elapsed. No member, act, response or event time is fabricated."""
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("SELECT pg_get_viewdef('public.cq_phases'::regclass)")
        original = cur.fetchone()[0].strip().rstrip(";")
        target = f"act_uuid='{window.hex}'::uuid"
        response = (
            f"CASE WHEN {target} THEN least(response_deadline,statement_timestamp()) "
            "ELSE response_deadline END"
            if response_due
            else "response_deadline"
        )
        cur.execute(
            "CREATE OR REPLACE VIEW public.cq_phases AS SELECT thread_uuid,organism_id,board_uuid,"
            f"act_uuid,phase,CASE WHEN {target} THEN least(deadline,statement_timestamp()) "
            f"ELSE deadline END AS deadline,ordinal,{response} AS response_deadline FROM ({original}) history",
            params=None,
        )
        try:
            with transaction.atomic():
                yield
        finally:
            cur.execute(
                "CREATE OR REPLACE VIEW public.cq_phases AS " + original, params=None
            )


@contextmanager
def subscriber_options_lapsed():
    migration = import_module("core.migrations.0072_discussion_boards")
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute(
            "SELECT prosrc FROM pg_proc WHERE oid='public.s015_entitlement_effective(uuid,text,timestamptz)'::regprocedure"
        )
        original = cur.fetchone()[0]
        limited = (
            "SELECT p_capability NOT IN ('CQ_COORDINATOR_INITIATION','CQ_TOTALS_ONLY') AND ("
            + original.strip()
            + ")"
        )
        cur.execute(migration.SE_HEADER + limited + "$$;", params=None)
        try:
            yield
        finally:
            cur.execute(migration.SE_HEADER + original + "$$;", params=None)


class CQClarityTests(TransactionTestCase):
    def owner(self, *, beta=False):
        lo, owner = fixtures.ordinary_birth(
            offering_choice="COMPLIMENTARY_BETA" if beta else "FREE"
        )
        return (
            lo,
            owner,
            {"credential": owner.credential, "organism_uuid": lo.organism_id},
        )

    def configure(self, scope, changes, previous=None):
        return cq.change_settings(
            **scope, request_uuid=uuid4(), predecessor=previous, changes=changes
        )

    def topic(self, scope, *, category="DISCUSSION", category_id=None, topic_type="SUGGESTION",
              title="Review the opening hours", markdown="Open on Saturdays"):
        """Start a fresh topic, optionally in a category already obtained on this board."""
        if category_id is None:
            disc.ensure_board(**scope, request_uuid=uuid4())
            categories = disc.board_categories(**scope)["categories"]
            category_id = next(
                c["category_uuid"] for c in categories if c["functional_code"] == category
            )
        thread = uuid4()
        result = posts.start_topic(
            **scope,
            category_uuid=category_id,
            thread_uuid=thread,
            request_uuid=uuid4(),
            post_uuid=uuid4(),
            title=title,
            markdown=markdown,
            topic_type=topic_type,
        )
        return scope | {"thread_uuid": thread}, category_id, result

    def facts(self, act):
        return disc._rows("SELECT * FROM public.core_cqact WHERE act_uuid=%s", [act])[0]

    def refused(self, code, text, func, **kwargs):
        with self.assertRaises(disc.DiscussionRefused) as caught:
            func(**kwargs)
        self.assertEqual((caught.exception.code, str(caught.exception)), (code, text))

    def test_free_settings_versions_and_authority(self):
        """DISC2-01. Free rules, subscriber selection gates, authoritative settings version."""
        lo, owner, scope = self.owner()
        initial = cq.settings(**scope)
        self.assertIsNone(initial["version"])
        self.assertEqual(
            initial["clarity_stage_control"]["label"], "Clarity stage before the vote"
        )
        self.assertIn(
            "every eligible member may vote", initial["clarity_stage_control"]["help"]
        )
        defaults = initial["settings"]
        self.assertEqual(
            [
                defaults[k]
                for k in (
                    "enabled",
                    "preparation_days",
                    "clarity_days",
                    "vote_days",
                    "clarity_stage",
                    "abstention",
                    "reporting",
                    "quorum_form",
                    "threshold_base",
                )
            ],
            [False, 3, 7, 7, True, False, "NAMED", "MAJORITY", "VOTES_CAST"],
        )
        member = fixtures.add_member(lo)
        self.refused(
            "CQ_SETTINGS_OFFICE",
            "Only this Living Organism's effective Coordinator may change its CQ settings.",
            cq.change_settings,
            **(scope | {"credential": member.credential}),
            changes={"enabled": True},
            predecessor=None,
            request_uuid=uuid4(),
        )
        for changes in ({"reporting": "TOTALS_ONLY"}, {"coordinator_initiation": True}):
            self.refused(
                "CQ_SUBSCRIBER",
                "Subscriber access is required to select this CQ option. An option already chosen stays in force.",
                cq.change_settings,
                **scope,
                changes=changes,
                predecessor=None,
                request_uuid=uuid4(),
            )
        self.refused(
            "CQ_LAW",
            "Identify the Article or Bylaw in force, its exact version, provision, body, decision, stage and denominator.",
            cq.change_settings,
            **scope,
            changes={"quorum_form": "NO_FLOOR"},
            predecessor=None,
            request_uuid=uuid4(),
        )
        for override in (
            {"quorum_form": "ABSOLUTE", "quorum_value": 2},
            {"quorum_form": "PERCENT", "quorum_value": 50},
            {"threshold": "FRACTION", "numerator": 1, "denominator": 3},
        ):
            self.refused(
                "CQ_LAW",
                cq.MESSAGES["CQ_LAW"],
                self.configure,
                scope=scope,
                changes=override,
            )
        changes = {
            "enabled": True,
            "abstention": True,
            "threshold": "FRACTION",
            "numerator": 2,
            "denominator": 3,
            "threshold_base": "PARTICIPANTS",
            "quorum_form": "ABSOLUTE",
            "quorum_value": 5,
        }
        first = self.configure(scope, changes)
        second = self.configure(scope, {"vote_days": 5}, first)
        self.refused(
            "CQ_SETTINGS_STALE",
            "Choose the current CQ settings version before changing it.",
            cq.change_settings,
            **scope,
            changes={"enabled": False},
            predecessor=first,
            request_uuid=uuid4(),
        )
        history = cq.settings_history(**scope)
        self.assertEqual([a["act_uuid"] for a in history], [first, second])
        self.assertEqual(history[0]["authority_source"]["kind"], "OFFICE")
        self.assertEqual(history[0]["actor_id"], owner.pk)
        self.assertEqual(
            history[1]["facts"]["previous"], history[0]["facts"]["settings"]
        )
        self.assertEqual(
            history[1]["facts"]["reporting_version"], initial["platform_version"]
        )
        event = disc._rows(
            """SELECT action, actor_id, enterer_identity_id, payload_fingerprint
               FROM public.core_livingorganismevent WHERE event_uuid=%s""",
            [first],
        )
        self.assertEqual(
            event,
            [
                {
                    "action": "RECORD_CQ",
                    "actor_id": None,
                    "enterer_identity_id": owner.pk,
                    "payload_fingerprint": history[0]["fingerprint"],
                }
            ],
        )

    def test_subscriber_initiation_and_reporting_history_on_lapse(self):
        """DISC2-02. Selected options survive a false entitlement read; author and initiator remain distinct."""
        lo, owner, scope = self.owner(beta=True)
        author = fixtures.add_member(lo)
        enabled = self.configure(scope, {"enabled": True})
        circle, coordinator = discussion_support.independent_circle(lo, owner)
        circle_scope = scope | {"circle_uuid": circle.circle_uuid}
        member = models.OrganismMembership.objects.get(
            living_organism=lo, identity=author
        )
        join = circle_commands.request_place(
            **(circle_scope | {"credential": author.credential}),
            membership_uuid=member.membership_uuid,
            request_uuid=uuid4(),
            consent="Participate in this Circle",
        )
        circle_commands.admit(
            **(circle_scope | {"credential": coordinator.credential}),
            request_uuid=join.request_uuid,
        )
        own, _, topic = self.topic(circle_scope | {"credential": author.credential})
        coord = own | {"credential": coordinator.credential}
        request = coord | {"version_uuid": topic["version"], "request_uuid": uuid4()}
        self.refused(
            "CQ_INITIATOR",
            "Only this Suggestion's author, or a Coordinator where this Living Organism allows it, may put it forward for CQ.",
            cq.put_forward,
            **request,
        )
        self.assertEqual(posts.read_thread(**own)["topic"]["record_kind"], "ORDINARY")
        picked = self.configure(
            scope, {"coordinator_initiation": True, "reporting": "TOTALS_ONLY"}, enabled
        )
        with subscriber_options_lapsed():
            fact = self.facts(picked)
            trace = fact["facts"]["reporting_rule"]
            self.assertEqual(
                (trace["old"], trace["new"], fact["facts"]["decision"]),
                ("NAMED", "TOTALS_ONLY", str(picked)),
            )
            self.assertNotEqual(trace["old_fingerprint"], trace["new_fingerprint"])
            self.assertEqual(fact["facts"]["reporting_version"], str(picked))
            act = cq.put_forward(**request)
            self.assertEqual(cq.put_forward(**request), act)
            got = self.facts(act)
            self.assertEqual(
                (
                    got["actor_id"],
                    got["facts"]["author"],
                    got["authority_source"]["kind"],
                ),
                (coordinator.pk, author.pk, "OFFICE"),
            )
            self.assertEqual(
                posts.read_thread(**own)["topic"]["record_kind"], "GOVERNANCE"
            )
            self.assertEqual(
                disc._rows(
                    "SELECT record_kind FROM public.core_discthread WHERE thread_uuid=%s",
                    [topic["thread"]],
                )[0]["record_kind"],
                "ORDINARY",
            )
            self.assertEqual(
                cq.settings(**scope)["settings"]["reporting"], "TOTALS_ONLY"
            )

        effective = support.database_now() + timedelta(days=1)
        later = cq.change_settings(
            **scope,
            request_uuid=uuid4(),
            predecessor=picked,
            changes={"reporting": "NAMED"},
            reporting_effective_from=effective,
        )
        self.assertEqual(cq.settings(**scope)["reporting_version"], str(picked))
        operative = disc._rows(
            "SELECT public.cq_reporting_at(%s,%s) AS rule", [lo.pk, effective]
        )[0]["rule"]
        self.assertEqual(
            (operative["version"], operative["value"]), (str(later), "NAMED")
        )
        rule = disc._rows(
            "SELECT decision,effective_from,evidence_reference FROM public.cq_reporting_rules WHERE version=%s",
            [str(later)],
        )[0]
        self.assertEqual(
            (rule["decision"], rule["effective_from"], rule["evidence_reference"]),
            (later, effective, "cq-settings-decision:" + str(later)),
        )

    test_subscriber_initiation_and_reporting_history_on_lapse.ISOLATION_FULL_CAPTURE = (
        True
    )

    def test_timed_preparation_and_idempotent_rule_catch_up(self):
        """DISC2-03. Inactivity, unprepared progression, original source and exactly-once processing."""
        lo, owner, scope = self.owner()
        enabled = self.configure(scope, {"enabled": True})
        journey, _, topic = self.topic(scope)
        entered = cq.put_forward(
            **journey, version_uuid=topic["version"], request_uuid=uuid4()
        )
        first = cq.phase(**journey)
        self.assertEqual(first["deadline"] - first["effective_at"], timedelta(days=3))
        self.assertEqual(cq.catch_up(credential=owner.credential), "Nothing is due.")
        other, _, next_topic = self.topic(scope)
        with due_at_phase_start():
            cq.put_forward(
                **other, version_uuid=next_topic["version"], request_uuid=uuid4()
            )
        self.configure(scope, {"enabled": False, "clarity_days": 3}, enabled)
        caller = fixtures.add_member(lo)
        before = models.LivingOrganismEvent.objects.count()
        self.assertEqual(
            cq.catch_up(credential=caller.credential), "Due work recorded."
        )
        self.assertEqual(cq.catch_up(credential=owner.credential), "Nothing is due.")
        self.assertEqual(models.LivingOrganismEvent.objects.count(), before + 1)
        row = disc._rows(
            "SELECT * FROM public.core_cqact WHERE thread_uuid=%s AND operation='PREPARATION_DUE'",
            [other["thread_uuid"]],
        )[0]
        self.assertEqual(
            (
                row["authority_source"]["kind"],
                row["actor_id"],
                row["facts"]["prepared"],
            ),
            ("RULE", caller.pk, False),
        )
        new = cq.phase(**other)
        self.assertEqual(new["deadline"] - new["effective_at"], timedelta(days=7))
        prepared = cq.mark_prepared(**journey, request_uuid=uuid4())
        self.assertEqual(self.facts(prepared)["authority_source"]["kind"], "OFFICE")
        self.assertTrue(cq.phase(**journey)["prepared"])
        self.assertLess(self.facts(entered)["ordinal"], self.facts(prepared)["ordinal"])
        cq.withdraw(**journey, request_uuid=uuid4())
        self.assertEqual(cq.phase(**journey)["phase"], "WITHDRAWN")

    test_timed_preparation_and_idempotent_rule_catch_up.ISOLATION_FULL_CAPTURE = True

    def test_wording_revisions_confirmations_and_early_lock(self):
        """DISC2-04. Exact wording, unanimous early close, independent vote-opening floor and lineage."""
        lo, owner, scope = self.owner()
        people = [owner, fixtures.add_member(lo), fixtures.add_member(lo)]
        self.configure(scope, {"enabled": True})
        journey, category, topic = self.topic(scope)
        cq.put_forward(**journey, version_uuid=topic["version"], request_uuid=uuid4())
        cq.mark_prepared(**journey, request_uuid=uuid4())
        cq.confirm_clarity(
            **journey, version_uuid=topic["version"], request_uuid=uuid4()
        )
        self.refused(
            "CQ_RESPONSE_REPEAT",
            "You have already confirmed clarity for this CQ.",
            cq.confirm_clarity,
            **journey,
            version_uuid=topic["version"],
            request_uuid=uuid4(),
        )
        edit_args = journey | {
            "category_uuid": category,
            "post_uuid": topic["post"],
            "predecessor": topic["version"],
            "request_uuid": uuid4(),
            "markdown": "Open on the first Saturday of each month",
        }
        revised = posts.edit_post(**edit_args)
        self.assertEqual(
            revised["clarity_notice"],
            "The wording has changed. Clarity must be confirmed again on this version.",
        )
        self.assertEqual(cq.phase(**journey)["version"], revised["version"])
        self.refused(
            "CQ_VERSION",
            "Choose the current wording before recording your Clarity response.",
            cq.confirm_clarity,
            **journey,
            version_uuid=topic["version"],
            request_uuid=uuid4(),
        )
        for person in people[1:]:
            cq.confirm_clarity(
                **(journey | {"credential": person.credential}),
                version_uuid=revised["version"],
                request_uuid=uuid4(),
            )
        self.refused(
            "CQ_WINDOW",
            "The clarity period has not ended and not everyone is clear.",
            cq.finish_clarity,
            **journey,
            request_uuid=uuid4(),
        )
        cq.confirm_clarity(
            **journey, version_uuid=revised["version"], request_uuid=uuid4()
        )
        opening = uuid4()
        before = models.LivingOrganismEvent.objects.count()
        self.assertEqual(cq.finish_clarity(**journey, request_uuid=opening), opening)
        self.assertEqual(cq.finish_clarity(**journey, request_uuid=opening), opening)
        self.assertEqual(models.LivingOrganismEvent.objects.count(), before + 1)
        self.assertEqual(cq_reads.vote_status(**journey)["ballot"], opening)
        self.assertEqual(self.facts(opening)["operation"], "PROCEED")
        self.refused(
            "CQ_NOT_READY",
            cq.MESSAGES["CQ_NOT_READY"],
            cq.open_vote,
            **journey,
            request_uuid=uuid4(),
        )
        ready = self.facts(opening)["facts"]
        self.assertEqual(
            (
                ready["phase"],
                ready["version"],
                len(ready["voting_pool"]),
                ready["required"],
            ),
            ("VOTING", str(revised["version"]), 3, 2),
        )
        self.refused(
            "CQ_LOCKED",
            "This wording is locked because clarity was reached on it. Start a new Suggestion to change it.",
            posts.edit_post,
            **(
                edit_args | {"request_uuid": uuid4(), "predecessor": revised["version"]}
            ),
        )
        linked, _, new = self.topic(scope)
        link = cq.put_forward(
            **linked,
            version_uuid=new["version"],
            previous_thread=topic["thread"],
            request_uuid=uuid4(),
        )
        self.assertEqual(
            self.facts(link)["facts"]["previous_thread"], str(topic["thread"])
        )
        self.assertEqual(cq.phase(**linked)["phase"], "PREPARATION")

    def test_at_deadline_unclear_members_and_free_clarity_setting(self):
        """DISC2-05. At expiry CDPT permits proceeding despite a question; free No admits the whole electorate."""
        lo, owner, scope = self.owner()
        people = [owner] + [fixtures.add_member(lo) for _ in range(3)]
        enabled = self.configure(scope, {"enabled": True})
        journey, first_category, topic = self.topic(scope)

        def unknown_ballot(target):
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                cq.answer(
                    **target,
                    ballot_uuid=uuid4(),
                    request_uuid=uuid4(),
                    choice="QUALIFIED",
                )

        cq.put_forward(**journey, version_uuid=topic["version"], request_uuid=uuid4())
        unknown_ballot(journey)
        prepared = cq.mark_prepared(**journey, request_uuid=uuid4())
        unknown_ballot(journey)
        for person in people[:3]:
            cq.confirm_clarity(
                **(journey | {"credential": person.credential}),
                version_uuid=topic["version"],
                request_uuid=uuid4(),
            )
        cq.raise_clarity_question(
            **(journey | {"credential": people[3].credential}),
            version_uuid=topic["version"],
            request_uuid=uuid4(),
        )
        window = cq.phase(**journey)
        self.assertEqual(
            window["response_deadline"] - window["deadline"], timedelta(days=3)
        )
        self.refused(
            "CQ_SETTINGS_INPUT",
            cq.MESSAGES["CQ_SETTINGS_INPUT"],
            self.configure,
            scope=scope,
            changes={"response_days": 7},
            previous=enabled,
        )
        # Witness the due branch on this same real group, then roll it back
        # to exercise the independent Coordinator route during the response period.
        with transaction.atomic():
            with elapsed_clarity(prepared, response_due=True):
                self.refused(
                    "CQ_RESPONSE_DUE",
                    cq.MESSAGES["CQ_RESPONSE_DUE"],
                    cq.extend_clarity,
                    **journey,
                    request_uuid=uuid4(),
                )
                self.refused(
                    "CQ_RESPONSE_DUE",
                    cq.MESSAGES["CQ_RESPONSE_DUE"],
                    cq.withdraw,
                    **journey,
                    request_uuid=uuid4(),
                )
                self.refused(
                    "CQ_EDIT_DUE",
                    disc.MESSAGES["CQ_EDIT_DUE"],
                    posts.edit_post,
                    **journey,
                    category_uuid=first_category,
                    post_uuid=topic["post"],
                    predecessor=topic["version"],
                    markdown="Replace the motion after its response period",
                    request_uuid=uuid4(),
                )
                self.assertEqual(
                    cq.catch_up(credential=people[2].credential), "Due work recorded."
                )
                auto = self.facts(cq_reads.vote_status(**journey)["ballot"])
                self.assertEqual(
                    (
                        auto["authority_source"]["kind"],
                        auto["actor_id"],
                        auto["facts"]["clarity_gate"],
                        len(auto["facts"]["roster"]),
                    ),
                    ("RULE", people[2].pk, True, 3),
                )
                self.refused(
                    "CQ_VOTE_LOCKED",
                    disc.MESSAGES["CQ_VOTE_LOCKED"],
                    posts.edit_post,
                    **journey,
                    category_uuid=first_category,
                    post_uuid=topic["post"],
                    predecessor=topic["version"],
                    markdown="Change after rule opening",
                    request_uuid=uuid4(),
                )
                cause = self.facts(auto["request"]["trigger_act"])
                self.assertEqual(
                    (cause["operation"], cause["request"]["window"]),
                    ("RESPONSE_DUE", str(prepared)),
                )
                self.assertEqual(
                    cq.catch_up(credential=owner.credential), "Nothing is due."
                )
            transaction.set_rollback(True)

        def edit_wording():
            return posts.edit_post(
                **journey,
                category_uuid=first_category,
                post_uuid=topic["post"],
                predecessor=topic["version"],
                markdown="Open one hour earlier",
                request_uuid=uuid4(),
            )

        with elapsed_clarity(prepared, response_due=False):
            self.refused(
                "CQ_WINDOW_LOCKED", disc.MESSAGES["CQ_WINDOW_LOCKED"], edit_wording
            )
            for action, expected in (
                (cq.extend_clarity, "CLARITY"),
                (cq.close_without_vote, "CLOSED"),
            ):
                with transaction.atomic():
                    act = action(**journey, request_uuid=uuid4())
                    self.assertEqual(self.facts(act)["facts"]["phase"], expected)
                    if action == cq.extend_clarity:
                        extension = cq.phase(**journey)
                        self.assertGreaterEqual(
                            extension["deadline"], window["deadline"]
                        )
                        self.assertEqual(
                            extension["deadline"],
                            self.facts(act)["effective_at"] + timedelta(days=7),
                        )
                        self.assertEqual(
                            extension["response_deadline"] - extension["deadline"],
                            timedelta(days=3),
                        )
                        self.assertEqual(
                            cq.catch_up(credential=owner.credential), "Nothing is due."
                        )
                        revised = edit_wording()
                        self.assertEqual(
                            cq.phase(**journey)["version"], revised["version"]
                        )
                    else:
                        self.refused(
                            "CQ_CLOSED_LOCKED",
                            disc.MESSAGES["CQ_CLOSED_LOCKED"],
                            edit_wording,
                        )
                        self.assertEqual(
                            cq_reads.result_post(**journey, result_uuid=act)["count"][
                                "kind"
                            ],
                            "NO_DECISION_COORDINATOR_CLOSED",
                        )
                    transaction.set_rollback(True)
            self.refused(
                "CQ_FINISH_OFFICE",
                cq.MESSAGES["CQ_FINISH_OFFICE"],
                cq.finish_clarity,
                **(journey | {"credential": people[1].credential}),
                request_uuid=uuid4(),
            )
            vote = cq.finish_clarity(**journey, request_uuid=uuid4())
            facts = self.facts(vote)["facts"]
        self.refused(
            "CQ_SAT_OUT",
            "You did not confirm clarity on this wording, so you sit out this vote.",
            cq.answer,
            **(journey | {"credential": people[3].credential}),
            ballot_uuid=vote,
            request_uuid=uuid4(),
            choice="QUALIFIED",
        )
        self.assertEqual(
            (len(facts["eligible"]), facts["required"], len(facts["voting_pool"])),
            (4, 3, 3),
        )
        self.assertEqual(
            next(
                x["response"]
                for x in facts["responses"]
                if x["member"]["person"] == people[3].pk
            ),
            "QUESTION_UNCONFIRMED",
        )
        stalled, category, silent = self.topic(scope)
        cq.put_forward(**stalled, version_uuid=silent["version"], request_uuid=uuid4())
        window_id = cq.mark_prepared(**stalled, request_uuid=uuid4())
        with elapsed_clarity(window_id, response_due=True):
            self.assertEqual(
                cq.catch_up(credential=people[1].credential), "Due work recorded."
            )
        self.refused(
            "CQ_CLOSED_LOCKED",
            disc.MESSAGES["CQ_CLOSED_LOCKED"],
            posts.edit_post,
            **stalled,
            category_uuid=category,
            post_uuid=silent["post"],
            predecessor=silent["version"],
            markdown="A different motion",
            request_uuid=uuid4(),
        )
        outcome = cq_reads.vote_status(**stalled)
        self.assertIsNone(outcome["ballot"])
        self.assertEqual(
            outcome["result"]["count"]["kind"], "NO_DECISION_CLARITY_THRESHOLD"
        )
        self.assertIn(
            "0 of 4 confirmed clarity. 3 confirmations were required.",
            outcome["result"]["markdown"],
        )
        self.assertEqual(outcome["result"]["post_number"], 2)
        self.assertNotIn("participants", outcome["result"])
        self.assertEqual(
            cq_reads.reporting_decision(**stalled, result_uuid=outcome["result_uuid"])[
                "origin"
            ],
            "PLATFORM",
        )
        posts.reply(
            **stalled,
            category_uuid=category,
            post_uuid=uuid4(),
            request_uuid=uuid4(),
            markdown="Acknowledge the outcome.",
        )
        self.assertEqual(
            [p["post_number"] for p in posts.read_thread(**stalled)["posts"]], [1, 2, 3]
        )
        self.configure(scope, {"clarity_stage": False}, enabled)
        other, category, second = self.topic(scope)
        edit = other | {
            "category_uuid": category,
            "post_uuid": second["post"],
            "markdown": "On Sundays instead",
        }
        revised = posts.edit_post(
            **edit, predecessor=second["version"], request_uuid=uuid4()
        )
        with due_at_phase_start():
            cq.put_forward(
                **other, version_uuid=revised["version"], request_uuid=uuid4()
            )
        # Same preparation and roster; change only whether members can read the motion.
        with transaction.atomic():
            posts.set_post_hidden(
                **other,
                category_uuid=category,
                post_uuid=second["post"],
                hidden=True,
                as_coordinator=False,
                request_uuid=uuid4(),
            )
            self.assertEqual(
                cq.catch_up(credential=people[1].credential), "Due work recorded."
            )
            invisible = cq_reads.vote_status(
                **(other | {"credential": people[1].credential})
            )
            self.assertIsNone(invisible["ballot"])
            self.assertEqual(
                invisible["result"]["count"]["kind"], "NO_DECISION_MOTION_UNAVAILABLE"
            )
            transaction.set_rollback(True)
        self.assertEqual(
            cq.catch_up(credential=people[1].credential), "Due work recorded."
        )
        self.assertEqual(cq.catch_up(credential=owner.credential), "Nothing is due.")
        unknown_ballot(other)
        ballot = cq_reads.vote_status(**other)["ballot"]
        act = self.facts(ballot)
        opening = act["facts"]
        self.assertEqual(
            (
                act["authority_source"]["kind"],
                act["authority_source"]["route"],
                act["actor_id"],
            ),
            ("RULE", "PREPARATION_DUE", people[1].pk),
        )
        self.assertEqual(act["authority_source"]["cause"], opening["pre_vote_act"])
        self.assertEqual(
            (
                opening["version"],
                opening["clarity"],
                opening["clarity_gate"],
                len(opening["roster"]),
            ),
            (str(revised["version"]), None, False, 4),
        )
        self.assertEqual(
            [row["phase"] for row in cq.phase(**other)["phase_dates"]],
            ["PREPARATION", "VOTING"],
        )
        self.refused(
            "CQ_NOT_READY",
            cq.MESSAGES["CQ_NOT_READY"],
            cq.open_vote,
            **other,
            request_uuid=uuid4(),
        )
        self.refused(
            "CQ_CONFIRM_PERIOD",
            cq.MESSAGES["CQ_CONFIRM_PERIOD"],
            cq.confirm_clarity,
            **other,
            version_uuid=revised["version"],
            request_uuid=uuid4(),
        )
        self.refused(
            "CQ_VOTE_LOCKED",
            disc.MESSAGES["CQ_VOTE_LOCKED"],
            posts.edit_post,
            **edit,
            predecessor=revised["version"],
            request_uuid=uuid4(),
        )
        cq.answer(
            **(other | {"credential": people[-1].credential}),
            ballot_uuid=ballot,
            choice="QUALIFIED",
            request_uuid=uuid4(),
        )

        small, _, fewer = self.topic(scope)
        cq.put_forward(**small, version_uuid=fewer["version"], request_uuid=uuid4())
        for person in people[2:]:
            cq.declare_conflict(
                **(small | {"credential": person.credential}),
                basis="Interested party",
                request_uuid=uuid4(),
            )
        trigger = cq.mark_prepared(**small, request_uuid=uuid4())
        status = cq_reads.vote_status(**small)
        self.assertEqual(
            (
                status["ballot"],
                status["result"]["count"]["eligible"],
                status["result"]["count"]["kind"],
            ),
            (None, 2, "NO_DECISION_FEWER_THAN_THREE"),
        )
        ended = self.facts(status["result_uuid"])
        self.assertEqual(
            (
                ended["operation"],
                ended["authority_source"]["kind"],
                ended["request"]["trigger_act"],
            ),
            ("NO_BALLOT", "RULE", str(trigger)),
        )
        self.assertEqual(
            posts.read_thread(**small)["posts"][-1]["post_uuid"], status["result_uuid"]
        )
        self.refused(
            "CQ_NOT_READY",
            cq.MESSAGES["CQ_NOT_READY"],
            cq.open_vote,
            **small,
            request_uuid=uuid4(),
        )
        self.assertEqual(cq.catch_up(credential=owner.credential), "Nothing is due.")

    test_at_deadline_unclear_members_and_free_clarity_setting.ISOLATION_FULL_CAPTURE = (
        True
    )

    def test_access_refusal_rollback_and_populated_reversal(self):
        """DISC2-06. Disclosure before role details, no backdoor through request reuse, immutable event pair."""
        _lo, _, scope = self.owner()
        key = self.configure(scope, {"enabled": True})
        stranger = support.register_test_learner(username="cq-outsider-" + uuid4().hex)
        private, _, topic = self.topic(scope, category="COORDINATION")
        self.refused(
            "CQ_FULL_AUDIENCE",
            "This category does not give the full board electorate access to the Suggestion.",
            cq.put_forward,
            **private,
            version_uuid=topic["version"],
            request_uuid=uuid4(),
        )
        with self.assertRaises(disc.DiscussionUnavailable) as denied:
            cq.put_forward(
                **(private | {"credential": stranger.credential}),
                version_uuid=topic["version"],
                request_uuid=key,
            )
        self.assertEqual(
            str(denied.exception), "This discussion is unavailable to you."
        )
        with self.assertRaises(disc.DiscussionUnavailable) as missing:
            cq.change_settings(
                **(scope | {"organism_uuid": uuid4()}),
                predecessor=None,
                changes={"enabled": True},
                request_uuid=key,
            )
        self.assertEqual(
            str(missing.exception), "This discussion is unavailable to you."
        )
        with (
            self.assertRaises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cur,
        ):
            cur.execute("DELETE FROM public.core_cqact WHERE act_uuid=%s", [key])
        with (
            self.assertRaises(IntegrityError) as forged,
            transaction.atomic(),
            connection.cursor() as cur,
        ):
            cur.execute(
                """INSERT INTO public.core_cqact(act_uuid,organism_id,operation,actor_id,actor_epoch,received_at,request,facts)
              SELECT %s,organism_id,'SETTINGS',actor_id,actor_epoch,statement_timestamp(),request,facts
              FROM public.core_cqact WHERE act_uuid=%s""",
                [uuid4(), key],
            )
        self.assertEqual(forged.exception.__cause__.diag.message_primary, "CQ_COMPLETE")
        with transaction.atomic():
            transient = self.configure(scope, {"enabled": False}, key)
            self.assertTrue(
                models.LivingOrganismEvent.objects.filter(event_uuid=transient).exists()
            )
            transaction.set_rollback(True)
        self.assertFalse(
            models.LivingOrganismEvent.objects.filter(event_uuid=transient).exists()
        )
        self.assertFalse(
            disc._rows("SELECT 1 FROM public.core_cqact WHERE act_uuid=%s", [transient])
        )
        self.assertEqual(cq.settings(**scope)["version"], key)

    test_access_refusal_rollback_and_populated_reversal.ISOLATION_FULL_CAPTURE = True

    def test_constitutional_entitlement_scope_and_retirement(self):
        """DISC2-07. A free LO cites a real Article. The exact version, body, motion and in-force window matter."""
        lo, owner, scope = self.owner()
        special, _, target = self.topic(scope)
        other, _, ordinary = self.topic(scope)

        skip, _, direct = self.topic(scope)
        doc = Document(owner)
        clause = doc.service.create_successor_version(
            identity=doc.author,
            resource_id=doc.name,
            content=(
                "[#1] On designated Living Organism motions, every eligible member may vote "
                "without confirming clarity. No minimum voting turnout applies. The "
                "designation may cover one motion or all Living Organism motions. "
                "The designated motions qualify with one third of participants in favour. "
                " [#2] The separately designated direct motion has no Clarity stage. Circle motions retain their usual rules. "
                "[#3] A designated Living Organism motion with a Clarity stage requires 80 percent to confirm."
            ),
            evidence_reference="disc2:constitutional-voting-right",
        )
        doc.transition("publish_library_resource")
        article = replace(
            fixtures.adoption(lo, doc, "ARTICLE"), version=clause.version_number
        )
        service = law_acts.OrganismLawService()
        enacted = service.adopt_law(fixtures.command(owner, article))
        self.assertEqual(
            (enacted.status, enacted.route), ("ADOPT", "SOLE_MEMBER_COORDINATOR")
        )

        citation = {
            "binding": str(article.proposal),
            "version": clause.pk,
            "provision": "1",
            "body": "LO",
            "decision": str(target["thread"]),
            "stage": "VOTE",
            "denominator": "All eligible members of the Living Organism",
        }
        stage = citation | {
            "stage": "CLARITY",
            "setting": "CLARITY_STAGE",
            "value": {"clarity_stage": False},
        }
        turnout = citation | {
            "setting": "CQPT",
            "value": {
                "quorum_form": "NO_FLOOR",
                "quorum_value": None,
                "waive_minimum": True,
            },
        }
        fraction = citation | {
            "setting": "CQT",
            "value": {"threshold": "FRACTION", "numerator": 1, "denominator": 3},
        }
        gate = citation | {
            "stage": "CLARITY",
            "setting": "CLARITY_GATE",
            "value": {"clarity_gate": False},
        }
        skip_stage = stage | {"provision": "2", "decision": str(direct["thread"])}
        entries = [gate, turnout, fraction, skip_stage]
        changes = {
            "enabled": True,
            "threshold_base": "PARTICIPANTS",
            "quorum_form": "ABSOLUTE",
            "quorum_value": 4,
            "threshold": "FRACTION",
            "numerator": 1,
            "denominator": 2,
            "provisions": entries,
        }
        law_message = cq.MESSAGES["CQ_LAW"]
        for wrong in (
            [turnout | {"version": doc.first.pk}],
            [turnout | {"stage": "CLARITY"}],
            [stage, stage | {"decision": "ALL_DECISIONS"}],
        ):
            self.refused(
                "CQ_LAW",
                law_message,
                self.configure,
                scope=scope,
                changes=changes | {"provisions": wrong},
            )
        for uncited in (
            {"quorum_form": "PERCENT", "quorum_value": 40},
            {"numerator": 1, "denominator": 3},
        ):
            self.refused(
                "CQ_LAW",
                law_message,
                self.configure,
                scope=scope,
                changes=changes | {"provisions": [stage]} | uncited,
            )
        # This Article removes the minimum. A scoped numerical choice must still
        # expressly waive it before it can require just two of three answers.
        for waived in (False, True):
            partial = turnout | {
                "value": {
                    "quorum_form": "ABSOLUTE",
                    "quorum_value": 2,
                    "waive_minimum": waived,
                }
            }
            scoped = changes | {"provisions": [partial]}
            if not waived:
                self.refused(
                    "CQ_LAW", law_message, self.configure, scope=scope, changes=scoped
                )
                continue
            with transaction.atomic():
                self.configure(scope, scoped)
                tally = disc._rows(
                    """SELECT public.cq_count_decision(public.cq_scoped_settings(public.cq_settings(%s),
                    %s,%s,%s,'VOTE',statement_timestamp()),3,2,0,0) AS tally""",
                    [
                        lo.pk,
                        lo.organism_id,
                        disc._board_id(lo.organism_id, None),
                        target["thread"],
                    ],
                )[0]["tally"]
                self.assertEqual((tally["required"], tally["kind"]), (2, "QUALIFIED"))
                transaction.set_rollback(True)
        first = self.configure(scope, changes)
        self.assertEqual(self.facts(first)["facts"]["settings"]["provisions"], entries)
        member = fixtures.add_member(lo)
        departing = fixtures.add_member(lo)
        people = {owner.pk, member.pk, departing.pk}

        def initiate(journey, topic):
            key = cq.put_forward(
                **journey, version_uuid=topic["version"], request_uuid=uuid4()
            )
            return self.facts(key)["facts"]["settings"]

        target_rules = initiate(special, target)
        other_rules = initiate(other, ordinary)
        self.assertEqual(
            (target_rules["clarity_stage"], target_rules["clarity_gate"]), (True, False)
        )
        self.assertTrue(other_rules["clarity_gate"])
        self.assertFalse(initiate(skip, direct)["clarity_stage"])
        trigger = cq.mark_prepared(**skip, request_uuid=uuid4())
        direct_open = self.facts(cq_reads.vote_status(**skip)["ballot"])
        self.assertEqual(
            (
                direct_open["authority_source"]["kind"],
                direct_open["request"]["trigger_act"],
                direct_open["facts"]["clarity"],
                direct_open["facts"]["clarity_provisions"],
            ),
            ("RULE", str(trigger), None, [skip_stage]),
        )
        prepared = cq.mark_prepared(**special, request_uuid=uuid4())
        self.assertEqual(cq.phase(**special)["phase"], "CLARITY")
        cq.confirm_clarity(
            **special, request_uuid=uuid4(), version_uuid=target["version"]
        )
        cq.raise_clarity_question(
            **(special | {"credential": member.credential}),
            request_uuid=uuid4(),
            version_uuid=target["version"],
        )
        self.refused(
            "CQ_WINDOW",
            cq.MESSAGES["CQ_WINDOW"],
            cq.finish_clarity,
            **special,
            request_uuid=uuid4(),
        )
        with elapsed_clarity(prepared, response_due=True):
            self.assertEqual(
                cq.catch_up(credential=member.credential), "Due work recorded."
            )
        target_ballot = cq_reads.vote_status(**special)["ballot"]
        opened = self.facts(target_ballot)["facts"]
        clarity_end = self.facts(opened["pre_vote_act"])["facts"]
        self.assertEqual(
            (
                clarity_end["clarity_gate"],
                len(clarity_end["confirmed"]),
                clarity_end["required"],
            ),
            (False, 1, 2),
        )
        self.assertEqual({m["person"] for m in opened["roster"]}, people)
        self.assertEqual(opened["rules"]["applied_provisions"], [turnout, fraction])
        self.assertEqual(
            (
                opened["clarity_gate"],
                opened["rules"]["clarity_stage"],
                opened["clarity_provisions"],
            ),
            (False, True, [gate]),
        )
        self.assertEqual(
            (opened["rules"]["quorum_form"], opened["rules"]["waive_minimum"]),
            ("NO_FLOOR", True),
        )
        for voter, answer in (
            (owner, "QUALIFIED"),
            (member, "NOT_QUALIFIED"),
            (departing, "NOT_QUALIFIED"),
        ):
            cq.answer(
                **(special | {"credential": voter.credential}),
                ballot_uuid=target_ballot,
                choice=answer,
                request_uuid=uuid4(),
            )
        qualified = cq_reads.vote_status(**special)["result"]["count"]
        self.assertEqual(
            (
                qualified["required"],
                qualified["qualified"],
                qualified["not_qualified"],
                qualified["kind"],
            ),
            (0, 1, 2, "QUALIFIED"),
        )
        cq.mark_prepared(**other, request_uuid=uuid4())
        for voter in (owner, member, departing):
            cq.confirm_clarity(
                **(other | {"credential": voter.credential}),
                version_uuid=ordinary["version"],
                request_uuid=uuid4(),
            )
        normal = self.facts(cq.finish_clarity(**other, request_uuid=uuid4()))["facts"][
            "rules"
        ]
        self.assertEqual(
            (
                normal["quorum_form"],
                normal["quorum_value"],
                normal["threshold_base"],
                normal["denominator"],
                normal["applied_provisions"],
            ),
            ("ABSOLUTE", 4, "PARTICIPANTS", 2, []),
        )
        interactive, _, motion = self.topic(scope)
        initiate(interactive, motion)
        interactive_window = cq.mark_prepared(**interactive, request_uuid=uuid4())
        # The vote exception is LO-wide. Free Off applies to the Circle too.
        broad = turnout | {"decision": "ALL_DECISIONS"}
        broad_version = self.configure(
            scope, {"provisions": [broad], "clarity_stage": False}, first
        )
        circle = circle_commands.form(
            **scope,
            circle_uuid=uuid4(),
            kind="GENERIC",
            purpose="Test the scope of an LO Article",
            request_uuid=uuid4(),
        )
        circle_journey, _, circle_topic = self.topic(
            scope | {"circle_uuid": circle.circle_uuid}
        )
        self.assertFalse(initiate(circle_journey, circle_topic)["clarity_stage"])
        circle_rules = disc._rows(
            "SELECT public.cq_scoped_settings(public.cq_settings(%s),%s,%s,%s,'VOTE',statement_timestamp()) AS rules",
            [
                lo.pk,
                lo.organism_id,
                disc._board_id(lo.organism_id, circle.circle_uuid),
                circle_topic["thread"],
            ],
        )[0]["rules"]
        self.assertEqual(
            (circle_rules["quorum_value"], circle_rules["applied_provisions"]), (4, [])
        )
        pending, _, later = self.topic(scope)
        inert, _, inactive = self.topic(scope)
        # Keep two actual vote provisions, plus two Clarity provisions on a different Off motion.
        pending_law = turnout | {"decision": str(later["thread"])}
        interactive_law = turnout | {"decision": str(motion["thread"])}
        off_gate = gate | {"decision": str(inactive["thread"])}
        off_cdpt = off_gate | {
            "provision": "3",
            "setting": "CDPT",
            "value": {"cdpt_percent": 80},
        }
        self.configure(
            scope,
            {"provisions": [pending_law, interactive_law, off_gate, off_cdpt]},
            broad_version,
        )
        self.assertFalse(initiate(pending, later)["clarity_stage"])

        # Retire under existing LAW authority: one actual departure leaves an eligible pair.
        membership = models.OrganismMembership.objects.get(
            living_organism=lo, identity=departing
        )
        at = support.database_now()
        membership_service = organism_membership_service.OrganismMembershipService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=support.database_now,
        )
        membership_service.leave_organism(
            organism_membership_service.LeaveOrganismCommand(
                credential=departing.credential,
                membership_uuid=membership.membership_uuid,
                confirmed=True,
                reason_class="VOLUNTARY",
                occurred_at=at,
                effective_at=at,
                idempotency_key=uuid4().hex,
                request_reference="disc2:leave:" + uuid4().hex,
            )
        )
        retirement = replace(
            article,
            proposal=uuid4(),
            predecessor=article.proposal,
            operation="RETIRE",
            intent="RETIRE",
        )
        self.assertEqual(
            service.adopt_law(fixtures.command(owner, retirement)).status, "AGREE"
        )
        ended = service.adopt_law(fixtures.command(member, retirement))
        self.assertEqual((ended.status, ended.route), ("RETIRE", "UNANIMOUS_PAIR"))
        # Restore three eligible people so the recorded outcome isolates the retired law.
        replacement = fixtures.add_member(lo)
        # These expired declarations govern a stage that this motion does not use.
        self.assertEqual(initiate(inert, inactive)["applied_provisions"], [])
        cq.mark_prepared(**inert, request_uuid=uuid4())
        inert_open = self.facts(cq_reads.vote_status(**inert)["ballot"])["facts"]
        self.assertEqual(
            (inert_open["clarity_provisions"], len(inert_open["roster"])), ([], 3)
        )

        for voter in (owner, member, replacement):
            cq.confirm_clarity(
                **(interactive | {"credential": voter.credential}),
                request_uuid=uuid4(),
                version_uuid=motion["version"],
            )
        rejected = uuid4()
        with elapsed_clarity(interactive_window, response_due=False):
            self.refused(
                "CQ_LAW",
                law_message,
                cq.finish_clarity,
                **interactive,
                request_uuid=rejected,
            )
            self.assertEqual(cq.phase(**interactive)["phase"], "CLARITY")
            self.assertFalse(
                models.LivingOrganismEvent.objects.filter(event_uuid=rejected).exists()
            )
            cq.extend_clarity(**interactive, request_uuid=uuid4())
        prepared = cq.mark_prepared(**pending, request_uuid=uuid4())
        status = cq_reads.vote_status(**pending)
        closed = self.facts(status["result_uuid"])
        self.assertIsNone(status["ballot"])
        self.assertEqual(
            (
                closed["operation"],
                closed["request"]["trigger_act"],
                closed["authority_source"]["kind"],
                closed["facts"]["invalid_provisions"],
                status["result"]["count"]["kind"],
            ),
            (
                "NO_BALLOT",
                str(prepared),
                "RULE",
                [pending_law],
                "NO_DECISION_PROVISION_ENDED",
            ),
        )
        for presented in (
            status["result"],
            cq_reads.result_post(**pending, result_uuid=status["result_uuid"]),
            posts.read_thread(**pending)["posts"][-1],
        ):
            self.assertEqual(
                (presented["post_uuid"], presented["invalid_provisions"]),
                (status["result_uuid"], [pending_law]),
            )
        self.assertTrue(
            models.LivingOrganismEvent.objects.filter(
                event_uuid=status["result_uuid"]
            ).exists()
        )
        self.assertEqual(cq.phase(**special)["phase"], "RESULT")
        self.assertEqual(self.facts(first)["facts"]["settings"]["provisions"], entries)

    test_constitutional_entitlement_scope_and_retirement.ISOLATION_FULL_CAPTURE = True

    def contended_commands(self, first, second, *, advisory=False):
        """Commit the first command only after a distinct backend demonstrably waits on it."""
        backend = Future()

        def contender():
            try:
                with connection.cursor() as cur:
                    cur.execute("SET lock_timeout = '15s'")
                    cur.execute("SET statement_timeout = '20s'")
                    cur.execute("SELECT pg_backend_pid()")
                    backend.set_result(cur.fetchone()[0])
                return second()
            except Exception as exc:
                if not backend.done():
                    backend.set_exception(exc)
                raise
            finally:
                connections.close_all()

        # Exit the transaction before joining the competing thread, including on failure.
        with ThreadPoolExecutor(max_workers=1) as pool, transaction.atomic():
            committed = first()
            waiting = pool.submit(contender)
            pid = backend.result(timeout=10)
            self.assertNotEqual(pid, query_one("SELECT pg_backend_pid()"))
            end = monotonic() + 6
            while monotonic() < end:
                blockers = disc._rows(
                    """SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s)) AS blocked,
                       EXISTS(SELECT FROM pg_locks WHERE pid=%s AND NOT granted
                              AND locktype='advisory') AS advisory""",
                    [pid, pid],
                )[0]
                if blockers["blocked"] and (not advisory or blockers["advisory"]):
                    break
                if waiting.done():
                    waiting.result()  # Surface a premature refusal or worker failure.
                    self.fail(
                        "The second command finished before the expected lock wait."
                    )
                wait([waiting], timeout=0.02)
            else:
                self.fail(
                    "The second database connection never waited on the first as required."
                )
        return committed, waiting

    def test_competing_wording_completion_and_due_catch_up(self):
        """DISC2-08. Both revision/finish orders and two catch-up callers contend on real connections."""
        lo, owner, scope = self.owner()
        author, observer = fixtures.add_member(lo), fixtures.add_member(lo)
        people = (owner, author, observer)
        self.configure(scope, {"enabled": True})
        journey, category, topic = self.topic(scope | {"credential": author.credential})
        coord = journey | {"credential": owner.credential}
        cq.put_forward(**journey, version_uuid=topic["version"], request_uuid=uuid4())
        cq.mark_prepared(**coord, request_uuid=uuid4())

        def confirm_everyone(version):
            for person in people:
                cq.confirm_clarity(
                    **(journey | {"credential": person.credential}),
                    version_uuid=version,
                    request_uuid=uuid4(),
                )

        confirm_everyone(topic["version"])
        edit = partial(
            posts.edit_post, **journey, category_uuid=category, post_uuid=topic["post"]
        )
        finish = partial(cq.finish_clarity, **coord)
        lost_finish = uuid4()
        revised, loser = self.contended_commands(
            partial(
                edit,
                predecessor=topic["version"],
                request_uuid=uuid4(),
                markdown="Open on Sundays",
            ),
            partial(finish, request_uuid=lost_finish),
        )
        self.refused(
            "CQ_WINDOW",
            "The clarity period has not ended and not everyone is clear.",
            loser.result,
        )
        after = cq.phase(**coord)
        self.assertEqual(
            (after["phase"], after["version"]), ("CLARITY", revised["version"])
        )

        confirm_everyone(revised["version"])
        lost_edit = uuid4()
        finished, loser = self.contended_commands(
            partial(finish, request_uuid=uuid4()),
            partial(
                edit,
                predecessor=revised["version"],
                request_uuid=lost_edit,
                markdown="Open on Fridays",
            ),
        )
        self.refused(
            "CQ_LOCKED",
            "This wording is locked because clarity was reached on it. "
            "Start a new Suggestion to change it.",
            loser.result,
        )
        current = posts.read_thread(**journey)["posts"][0]
        ready = self.facts(finished)["facts"]
        self.assertEqual(
            (current["version_uuid"], current["version_number"], current["markdown"]),
            (revised["version"], 2, "Open on Sundays"),
        )
        self.assertEqual(
            (ready["phase"], ready["version"], ready["wording_fingerprint"]),
            ("VOTING", str(current["version_uuid"]), current["body_fingerprint"]),
        )
        self.assertEqual(
            {p["person"] for p in ready["confirmed"]}, {p.pk for p in people}
        )
        self.assertFalse(
            models.LivingOrganismEvent.objects.filter(
                event_uuid__in=[lost_finish, lost_edit]
            ).exists()
        )

        due, _, later = self.topic(scope)
        with due_at_phase_start():
            cq.put_forward(**due, version_uuid=later["version"], request_uuid=uuid4())
        before = models.LivingOrganismEvent.objects.count()
        wrote, other = self.contended_commands(
            partial(cq.catch_up, credential=author.credential),
            partial(cq.catch_up, credential=observer.credential),
            advisory=True,
        )
        self.assertEqual(
            (wrote, other.result()), ("Due work recorded.", "Nothing is due.")
        )
        self.assertEqual(cq.catch_up(credential=owner.credential), "Nothing is due.")
        self.assertEqual(models.LivingOrganismEvent.objects.count(), before + 1)
        self.assertEqual(cq.phase(**due)["phase"], "CLARITY")
        self.assertEqual(
            disc._rows(
                """SELECT a.actor_id, e.enterer_identity_id, a.authority_source->>'kind' AS source,
                      a.facts->'prepared' AS prepared FROM public.core_cqact a
               JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
               WHERE a.thread_uuid=%s AND a.operation='PREPARATION_DUE'""",
                [later["thread"]],
            ),
            [
                {
                    "actor_id": author.pk,
                    "enterer_identity_id": author.pk,
                    "source": "RULE",
                    "prepared": False,
                }
            ],
        )

    test_competing_wording_completion_and_due_catch_up.ISOLATION_FULL_CAPTURE = True
