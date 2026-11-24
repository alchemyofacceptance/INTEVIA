"""Plan box 8c: after a missed deadline a Board member records the result with one button.

Interim until the Clock's wake is qualified (owed row C1-228). No page read records anything.
"""
from unittest import mock
from uuid import uuid4

from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from src.intevia.services import cq, cq_reads
from tests import test_cq_clarity as clarity_support
from tests import test_discussion_posts as post_support
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, signed_in
from tests.test_uc2_ballot_page import assert_security_refusal
from tests.test_uc2_ballot_reads import open_board_vote, recorded_acts
from tests.test_uc2_pages import FIXTURE_HASHERS

CLOSED = "Voting has closed. The result has not been recorded yet."
# HG capture H: the warning before the press, and the line under "Board vote" says when voting closed.
WARNING = "Recording it also records any other result or deadline that is due in your organisations."
# The two receipts after the press, as accepted for box 8c, pinned apart from the production constants.
RECORDED = ("Due results are now recorded. This covered every vote and deadline that was due "
            "in all the organisations you belong to, not only this Topic.")
NOTHING_DUE = "Nothing was due. The result may already have been recorded."


def second_overdue_vote(case):
    """A second Suggestion on the same Board whose vote is also due, opened as ``open_board_vote`` opens one."""
    coordinator = case.people[1]
    support = post_support.DiscussionPostTests()
    args = support.board(case.owner, coordinator, case.board)
    topic = support.start(args, topic_type="SUGGESTION", markdown="Approve a second payment.")
    scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
    cq.put_forward(**scope, request_uuid=uuid4(), version_uuid=topic["version"])
    with clarity_support.due_at_phase_start():
        cq.mark_prepared(**scope, request_uuid=uuid4())
    return scope


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class DueResultTests(TransactionTestCase):
    """B8C-01 and B8C-02 (plan rows D11 and S13 at the route), with the wake off."""

    def endpoint(self, case):
        return reverse("discussion_record_result", args=(case.owner.organism_id, case.board.circle_uuid, case.thread))

    def due_form(self, case, response):
        """The vote section's text and the hidden fields of its "Record the result" form, if it has one."""
        page = Markup(response.content.decode())
        section, = page.select("section", id="vote")
        forms = page.select("form", within=section, method="post", action=self.endpoint(case))
        fields = [{node["attrs"]["name"]: node["attrs"]["value"] for node in page.select("input", within=form, type="hidden")}
                  for form in forms]
        labels = [button["text"].strip() for form in forms for button in page.select("button", within=form)]
        return section["text"], fields, labels

    def notices(self, response):
        """INTEVIA's status notices at the top of the page."""
        page = Markup(response.content.decode())
        return [node["text"].strip() for node in page.select("aside", role="status")]

    def test_b8c_01_the_result_is_recorded_only_by_the_explicit_press(self):
        """D11 and S13. Reading, a link, a preview and a bare post record nothing; one press records; a replay adds nothing."""
        case = open_board_vote(overdue=True)
        founder, coordinator, trustee = case.people
        before = recorded_acts(case.thread)
        client = signed_in(trustee)
        first = client.get(case.url)
        text, fields, labels = self.due_form(case, first)
        self.assertIn(CLOSED, text, "INTEVIA must say that voting has closed and no result is recorded yet.")
        self.assertEqual(labels, ["Record the result"], "A Board member must be offered one button to record the result.")
        self.assertIn(WARNING, text, "Before the press, INTEVIA must say that recording also records other due results.")
        self.assertIn("Voting closed · Closed ", text, "The line under the vote must say when voting closed (capture H).")
        self.assertNotIn("Awaiting result", text, "The ruled wording replaces \"Awaiting result\" once the deadline is known.")
        self.assertEqual(recorded_acts(case.thread), before, "Showing the button must record nothing.")
        url = self.endpoint(case)
        self.assertEqual(client.get(url).status_code, 405, "A link cannot record a result.")
        self.assertEqual(client.post(url, fields[0], HTTP_SEC_PURPOSE="prefetch").status_code, 400,
                         "A browser's preview of the form must not record a result.")
        self.assertEqual(client.post(url, {}).status_code, 400, "A post without this page's form must be refused.")
        self.assertEqual(recorded_acts(case.thread), before,
                         "A link, a preview and a bare post must each leave the vote exactly as it was.")

        # Advisor box 8 part 2, finding 3: with the CSRF field missing, the five-part refusal and nothing recorded.
        client.handler.enforce_csrf_checks = True
        assert_security_refusal(self, client.post(url, {name: value for name, value in fields[0].items()
                                                        if name != "csrfmiddlewaretoken"}),
                                "This request recorded no new vote action.")
        self.assertEqual(recorded_acts(case.thread), before, "A form whose security check fails must record nothing.")
        sent = client.post(url, fields[0])
        self.assertEqual((sent.status_code, sent.get("Location")), (303, case.url + "#vote"),
                         "The press must take the member back to this Topic's vote.")
        assert_private(self, sent)
        after = recorded_acts(case.thread)
        self.assertNotEqual(after, before, "The press must record the due result.")
        self.assertEqual(cq_reads.vote_status(**case.scope)["phase"], "RESULT", "This Topic's result must now be recorded.")
        shown = client.get(case.url)
        self.assertEqual(self.notices(shown), [RECORDED],
                         "After the press the page must say plainly that it covered all the member's organisations.")
        text, fields_after, labels = self.due_form(case, shown)
        self.assertEqual((labels, CLOSED in text), ([], False), "Once the result is recorded the button and its line must go.")

        again = client.post(url, fields[0])
        self.assertEqual(again.status_code, 303, "The same form sent again must be answered, not fail.")
        self.assertEqual(recorded_acts(case.thread), after, "A replayed press must record nothing more.")
        self.assertEqual(self.notices(client.get(case.url)), [NOTHING_DUE],
                         "A press with nothing due must say so.")
        self.assertEqual(self.due_form(case, signed_in(founder).get(case.url))[2], [],
                         "No other member is offered the button once the result is recorded.")

    def test_b8c_03_a_refusal_after_the_sweep_began_does_not_deny_what_was_recorded(self):
        """Capture O. Each due result commits on its own; a later refusal must not say that nothing was recorded."""
        case = open_board_vote(overdue=True)
        trustee = case.people[2]
        other = second_overdue_vote(case)
        client = signed_in(trustee)
        _, fields, _ = self.due_form(case, client.get(case.url))
        real, settled = cq._execute, []

        def execute(actor, owner, board, thread, operation, *rest):
            if operation == "SETTLE" and settled:
                raise cq.CQRefused("CQ_LAW")
            if operation == "SETTLE":
                settled.append(thread)
            return real(actor, owner, board, thread, operation, *rest)

        with mock.patch.object(cq, "_execute", side_effect=execute):
            refused = client.post(self.endpoint(case), fields[0])
        phases = sorted(cq_reads.vote_status(**(case.scope | {"thread_uuid": thread}))["phase"]
                        for thread in (case.thread, other["thread_uuid"]))
        self.assertEqual(phases, ["AWAITING_SETTLEMENT", "RESULT"],
                         "The first due result must stay recorded, and the refused one must not be.")
        self.assertEqual(refused.status_code, 409, "The refusal is still a refusal.")
        page = Markup(refused.content.decode())
        self.assertEqual([h["text"] for h in page.select("h1")], ["Not all due work could be recorded."],
                         "The heading must not claim that the press recorded nothing.")
        alert, = page.select("div", role="alert")
        lines = [p["text"] for p in page.select("p", within=alert)]
        self.assertEqual(lines[1:3], ["Some due work may already have been recorded before this stopped.",
                                      "Reopen the Topic to check where the vote stands."],
                         "The receipt must say that some work may be recorded and how to check (capture O).")
        self.assertEqual([line for line in lines if "recorded no" in line], [],
                         "No line may claim that nothing was recorded.")

    def test_b8c_04_an_unknown_outcome_after_the_sweep_began_takes_the_same_receipt(self):
        """Capture Q (D2). A database error after one due result committed: capture O's form, the unknown reason."""
        from django.db import OperationalError
        case = open_board_vote(overdue=True)
        trustee = case.people[2]
        other = second_overdue_vote(case)
        client = signed_in(trustee)
        _, fields, _ = self.due_form(case, client.get(case.url))
        real, settled = cq._execute, []

        def execute(actor, owner, board, thread, operation, *rest):
            if operation == "SETTLE" and settled:
                raise OperationalError("the connection was lost")
            if operation == "SETTLE":
                settled.append(thread)
            return real(actor, owner, board, thread, operation, *rest)

        with mock.patch.object(cq, "_execute", side_effect=execute):
            failed = client.post(self.endpoint(case), fields[0])
        phases = sorted(cq_reads.vote_status(**(case.scope | {"thread_uuid": thread}))["phase"]
                        for thread in (case.thread, other["thread_uuid"]))
        self.assertEqual(phases, ["AWAITING_SETTLEMENT", "RESULT"],
                         "The first due result must stay recorded, and the failed one must not be.")
        self.assertEqual(failed.status_code, 503, "An unknown outcome is still reported as one.")
        page = Markup(failed.content.decode())
        self.assertEqual([h["text"] for h in page.select("h1")], ["Not all due work could be recorded."],
                         "After the sweep began, an unknown outcome takes capture O's heading.")
        alert, = page.select("div", role="alert")
        self.assertEqual([p["text"] for p in page.select("p", within=alert)][:3], [
            "The service could not confirm the outcome. The cause is unknown.",
            "Some due work may already have been recorded before this stopped.",
            "Reopen the Topic to check where the vote stands."],
            "The unknown reason, then capture O's effect and next step.")

    def test_b8c_02_no_button_while_voting_is_open(self):
        """Before the deadline there is nothing to record: no line, no button, no flag."""
        case = open_board_vote()
        trustee = case.people[2]
        text, fields, labels = self.due_form(case, signed_in(trustee).get(case.url))
        self.assertEqual((labels, fields, CLOSED in text), ([], [], False),
                         "While voting is open the page must not offer to record a result.")
        self.assertNotIn("result_due", cq.board_topic_vote(**(case.scope | {"credential": trustee.credential})),
                         "The read must not mark an open vote as due.")
