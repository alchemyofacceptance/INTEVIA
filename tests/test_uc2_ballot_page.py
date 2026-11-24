"""Plan box 8b: a Board member answers an open vote on its Topic; the count shows on the Topic and the list.

Each test seats its own Board of three through the real routes (``open_board_vote``).
"""
from uuid import uuid4

from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from src.intevia.services import cq, cq_reads, discussion
from tests import test_law_acts as laws
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, signed_in
from tests.test_uc2_ballot_reads import open_board_vote
from tests.test_uc2_pages import FIXTURE_HASHERS, path


def answers(ballot):
    """Each recorded answer on one ballot: who and what, in order."""
    return discussion._rows("""SELECT actor_id, request->>'choice' AS choice FROM public.core_cqact
        WHERE operation='ANSWER' AND request->>'ballot'=%s ORDER BY ordinal""", [str(ballot)])


def assert_security_refusal(test, response, effect):
    """A failed CSRF check: refused, with INTEVIA's five-part message (Advisor box 8 part 2, finding 3; capture Q, D1)."""
    test.assertEqual(response.status_code, 403, "A form whose security check fails must be refused.")
    page = Markup(response.content.decode())
    test.assertEqual([h["text"] for h in page.select("h1")], ["The form could not be submitted."],
                     "The refusal must be INTEVIA's own page, not the generic security error.")
    alert, = page.select("div", role="alert")
    lines = [p["text"] for p in page.select("p", within=alert)]
    test.assertEqual(lines, ["The form's security check did not pass.", effect,
                             "Reopen the page and check the current wording and available actions.",
                             "If you need help, contact an Organisation Coordinator."],
                     "The refusal must give its reason, what happened, what to do next and who can help.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class BallotPageTests(TransactionTestCase):
    """B8B-01 to B8B-03 (plan row D10 and the page witnesses)."""

    def endpoint(self, case):
        return reverse("discussion_answer", args=(case.owner.organism_id, case.board.circle_uuid, case.thread))

    def vote_section(self, response):
        page = Markup(response.content.decode())
        section, = page.select("section", id="vote")
        return page, section

    def open_form(self, case, client):
        """Follow "Answer this vote" and return the ballot form's page and its hidden fields."""
        page, section = self.vote_section(client.get(case.url))
        link, = [a for a in page.select("a", within=section) if a["text"].strip() == "Answer this vote"]
        opened = client.get(link["attrs"]["href"])
        self.assertEqual(opened.status_code, 200, "The ballot form must open for the member it was offered to.")
        page = Markup(opened.content.decode())
        form, = page.select("form", method="post", action=self.endpoint(case))
        fields = {node["attrs"]["name"]: node["attrs"]["value"] for node in page.select("input", within=form, type="hidden")}
        return opened, page, form, fields

    def status(self, page):
        """The Topic's line under its title."""
        line, = [p["text"].strip() for p in page.select("p", classes=("meta",)) if p["text"].strip().startswith("Suggestion")]
        return line

    def as_reader(self, case, person):
        return case.scope | {"credential": person.credential}

    def test_b8b_01_a_member_answers_in_two_steps_and_sees_only_their_own_answer(self):
        """Rulings 1, 2 and 4. Button, then form; one recorded answer; the status counts it."""
        case = open_board_vote()
        founder, coordinator, trustee = case.people
        client = signed_in(trustee)
        first = client.get(case.url)
        page, section = self.vote_section(first)
        self.assertEqual([a["text"].strip() for a in page.select("a", within=section, classes=("button", "primary"))],
                         ["Answer this vote"], "An eligible member must be offered one primary button to answer.")
        self.assertEqual(page.select("form", action=self.endpoint(case)), [],
                         "The ballot form must not show until the member asks for it.")
        self.assertTrue(self.status(page).endswith(" · Voting open · 0 of 3 answered"),
                        "The line under the Topic's title must carry the vote's count-only status.")
        self.assertTrue(page.select("form", action=reverse("discussion_reply", args=(
            case.owner.organism_id, case.board.circle_uuid, case.thread))), "Reply stays the page's default form.")

        opened, page, form, fields = self.open_form(case, client)
        radios = page.select("input", within=form, type="radio", name="choice")
        self.assertEqual([node["attrs"]["value"] for node in radios], ["QUALIFIED", "NOT_QUALIFIED"],
                         "The form must offer the options this ballot captured: For and Against only.")
        self.assertEqual([label["text"].strip() for label in page.select("label", within=form)], ["For", "Against"],
                         "The options must read For and Against.")
        self.assertEqual([b["text"].strip() for b in page.select("button", within=form)], ["Submit answer"],
                         "The form's one submit button must read Submit answer.")
        self.assertEqual([(a["text"].strip(), a["attrs"]["href"]) for a in page.select("a", within=form)],
                         [("Cancel", case.url + "#vote")], "Cancel must lead back to the Topic's vote.")
        notice, = page.select("div", classes=("notice", "vote-feedback"))
        self.assertIn("Your answer is recorded once and cannot be changed.", notice["text"],
                      "INTEVIA's one box must say the answer is final before the member submits.")
        self.assertEqual(page.select("form", action=reverse("discussion_reply", args=(
            case.owner.organism_id, case.board.circle_uuid, case.thread))), [],
            "While the ballot form is open, no other form may show.")
        self.assertTrue([a for a in page.select("a") if a["text"].strip() == "Back to Board discussion"],
                        "The page keeps its way back while the form is open.")
        assert_private(self, opened)

        ballot = cq_reads.vote_status(**case.scope)["ballot"]
        # Advisor box 8 part 2, finding 3: with the CSRF field missing, the five-part refusal and nothing recorded.
        client.handler.enforce_csrf_checks = True
        unchecked = {name: value for name, value in fields.items() if name != "csrfmiddlewaretoken"}
        assert_security_refusal(self, client.post(self.endpoint(case), unchecked | {"choice": "QUALIFIED"}),
                                "This request recorded no answer.")
        self.assertEqual(answers(ballot), [], "A form whose security check fails must record nothing.")
        sent = client.post(self.endpoint(case), fields | {"choice": "QUALIFIED"})
        self.assertEqual((sent.status_code, sent.get("Location")), (303, case.url + "#vote"),
                         "A recorded answer must take the member back to this Topic's vote.")
        self.assertEqual(answers(ballot), [{"actor_id": trustee.pk, "choice": "QUALIFIED"}],
                         "Exactly one answer must be recorded: this member's, on this ballot.")
        page, section = self.vote_section(client.get(case.url))
        notice, = page.select("div", within=section, classes=("notice", "vote-feedback"))
        self.assertIn("Your answer is recorded.", notice["text"], "INTEVIA must confirm the recorded answer.")
        own, = page.select("div", within=section, id="own-answer")
        self.assertEqual(own["text"].strip(), "You answered For.", "The member must see their own answer.")
        self.assertEqual([a for a in page.select("a", within=section) if a["text"].strip() == "Answer this vote"], [],
                         "A member who has answered must not be offered the ballot again.")
        self.assertTrue(self.status(page).endswith(" · Voting open · 1 of 3 answered"),
                        "The status must count the new answer.")
        other, other_section = self.vote_section(signed_in(founder).get(case.url))
        self.assertEqual(other.select("div", within=other_section, id="own-answer"), [],
                         "Another member must not be shown this member's answer.")
        self.assertNotIn("You answered", other_section["text"], "Another member's page must not state any answer.")

        cq.answer(**self.as_reader(case, founder), ballot_uuid=ballot, choice="QUALIFIED", request_uuid=uuid4())
        # Capture N: the last answer goes through the real form, records the result, and keeps its receipt.
        last = signed_in(coordinator)
        _, _, _, fields = self.open_form(case, last)
        sent = last.post(self.endpoint(case), fields | {"choice": "NOT_QUALIFIED"}, follow=True)
        self.assertEqual(sent.redirect_chain, [(case.url + "#vote", 303)],
                         "The last answer must also take the member back to this Topic's vote.")
        self.assertEqual(cq_reads.vote_status(**case.scope, ballot_uuid=ballot)["phase"], "RESULT",
                         "The last answer must record the result with no further step.")
        self.assertEqual([row for row in answers(ballot) if row["actor_id"] == coordinator.pk],
                         [{"actor_id": coordinator.pk, "choice": "NOT_QUALIFIED"}],
                         "Exactly one answer must be recorded for the last voter.")
        page, section = self.vote_section(sent)
        self.assertNotIn("Voting open", self.status(page),
                         "Once the result is recorded the Topic no longer says voting is open.")
        self.assertIn("Decided", section["text"], "The last voter must see that the vote is decided.")
        notices = page.select("div", within=section, classes=("notice", "vote-feedback"))
        self.assertEqual([p["text"] for notice in notices for p in page.select("p", within=notice)],
                         ["Your answer is recorded."], "INTEVIA must confirm the last voter's recorded answer.")
        self.assertEqual([own["text"].strip() for own in page.select("div", within=section, id="own-answer")],
                         ["You answered Against."], "The last voter must see their own answer.")
        for reader in (client, signed_in(founder)):
            page, section = self.vote_section(reader.get(case.url))
            own, = page.select("div", within=section, id="own-answer")
            self.assertEqual(own["text"].strip(), "You answered For.",
                             "After the result each member who answered still sees their own answer.")
            self.assertNotIn("Against", section["text"], "No other member's answer may appear in their vote section.")

    def test_b8b_02_a_stale_form_records_nothing_and_a_replay_records_once(self):
        """D10. A form signed for the first attempt cannot answer the second; a replayed form answers once."""
        case = open_board_vote(changes={"abstention": True, "low_turnout": "NEW_CQ",
                                        "quorum_form": "ABSOLUTE", "quorum_value": 5})
        founder, coordinator, trustee = case.people
        client = signed_in(trustee)
        _, page, form, stale = self.open_form(case, client)
        self.assertEqual([n["attrs"]["value"] for n in page.select("input", within=form, type="radio")],
                         ["QUALIFIED", "NOT_QUALIFIED", "ABSTAIN"],
                         "A ballot whose rules allow abstention must offer Abstain last.")
        first = cq_reads.vote_status(**case.scope)["ballot"]
        _, _, _, used = self.open_form(case, client)
        self.assertEqual(client.post(self.endpoint(case), used | {"choice": "QUALIFIED"}).status_code, 303,
                         "The trustee's first answer goes through the real form.")
        # Three answers cannot reach this ballot's floor of five, so the rule opens a second attempt.
        for person in (founder, coordinator):
            cq.answer(**self.as_reader(case, person), ballot_uuid=first, choice="QUALIFIED", request_uuid=uuid4())
        second = cq_reads.vote_status(**case.scope)["ballot"]
        self.assertNotEqual(second, first, "The fixture must have opened a second attempt.")

        refused = client.post(self.endpoint(case), stale | {"choice": "NOT_QUALIFIED"})
        self.assertEqual(refused.status_code, 409, "A form signed for the earlier attempt must be refused.")
        self.assertEqual(answers(second), [], "The stale form must record nothing on the new ballot.")
        self.assertEqual(len(answers(first)), 3, "The stale form must add nothing to the settled ballot.")
        page = Markup(refused.content.decode())
        self.assertEqual([h["text"] for h in page.select("h1")], ["Your answer could not be recorded."])
        alert, = page.select("div", role="alert")
        self.assertEqual([p["text"] for p in page.select("p", within=alert)], [
            "This vote has closed, or a new vote has replaced the one this form was for.",
            "This request recorded no answer.",
            "Go back to the Topic to see where the vote stands. If a new vote has opened, you can answer it there.",
            "If you need help, contact a Board Coordinator.",
        ], "The refusal must say why, that nothing was recorded, what to do next and who can help.")
        self.assertTrue([a for a in page.select("a") if a["text"].strip() == "Back to Topic"],
                        "The refusal must offer the way back to the Topic.")
        assert_private(self, refused)
        # Capture N: the form already used on the first attempt, sent again unchanged, is refused too.
        replayed = client.post(self.endpoint(case), used | {"choice": "QUALIFIED"})
        self.assertEqual(replayed.status_code, 409,
                         "A used form for the earlier attempt, sent again after a new vote opened, must be refused.")
        alert, = Markup(replayed.content.decode()).select("div", role="alert")
        self.assertIn("This vote has closed, or a new vote has replaced the one this form was for.", alert["text"],
                      "The replayed form must get the same out-of-date reason.")
        self.assertEqual((len(answers(first)), answers(second)), (3, []),
                         "The replay must leave three answers on the first ballot and none on the second.")

        _, _, _, fresh = self.open_form(case, client)
        for _ in range(2):
            sent = client.post(self.endpoint(case), fresh | {"choice": "ABSTAIN"})
            self.assertEqual(sent.status_code, 303, "The current form, sent once or replayed, must be accepted.")
        self.assertEqual(answers(second), [{"actor_id": trustee.pk, "choice": "ABSTAIN"}],
                         "A replayed form must leave exactly one answer.")
        missing = signed_in(founder)
        _, _, _, fields = self.open_form(case, missing)
        self.assertEqual(missing.post(self.endpoint(case), fields).status_code, 409,
                         "A form sent with no answer chosen must be refused.")
        self.assertEqual(len(answers(second)), 1, "A form with no answer chosen must record nothing.")
        self.assertEqual(missing.get(self.endpoint(case)).status_code, 405, "A link cannot submit an answer.")

    def test_b8b_03_the_board_list_shows_the_count_and_no_participant(self):
        """Ruling 4 on the list. One count-only line under the Topic's date; no name in the read or the page."""
        case = open_board_vote()
        founder, coordinator, trustee = case.people
        ballot = cq_reads.vote_status(**case.scope)["ballot"]
        cq.answer(**self.as_reader(case, founder), ballot_uuid=ballot, choice="QUALIFIED", request_uuid=uuid4())
        board = {"organism_uuid": case.owner.organism_id, "circle_uuid": case.board.circle_uuid}
        counts = cq.board_open_vote_counts(credential=trustee.credential, **board)
        self.assertEqual(counts, {case.thread: {"eligible": 3, "answered": 1}},
                         "The list's read must hold this Topic's count and nothing else.")
        self.assertEqual(cq_reads.vote_status(**case.scope)["reporting_rule"]["value"], "NAMED",
                         "This witness needs the platform default, which reports by name.")
        names = [person.display_name for person in case.people]
        withheld = names + [str(person.identity_id) for person in case.people]
        self.assertEqual([value for value in withheld if value in repr(counts)], [],
                         "The list's read must carry no participant's name or identity.")
        response = signed_in(trustee).get(path(case.owner, case.board))
        page = Markup(response.content.decode())
        self.assertEqual([line["text"].strip() for line in page.select("div", classes=("vote-status-line",))],
                         ["Voting open · 1 of 3 answered"], "The list must show the count-only status under the date.")
        main, = page.select("main")
        self.assertEqual([name for name in (founder.display_name, coordinator.display_name) if name in main["text"]], [],
                         "The Board's discussion list must not name who has or has not answered.")
        outsider = laws.add_member(case.owner)
        self.assertEqual(cq.board_open_vote_counts(credential=outsider.credential, **board), {},
                         "A member who cannot open the Board's Topics must receive no count from this read.")
