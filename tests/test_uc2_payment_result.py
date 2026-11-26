"""Plan box 9: the payment result, readable on its own (storyboard scene 5, step A4; owed row C1-403).

A person who follows a result's link sees which body decided, the outcome, the wording decided and
when it took effect, whether or not they may open the Board's Topic. INTEVIA records the approval
of a payment; it does not move money. Rulings: HG capture Z of 24 Nov 2026, Q1 to Q4.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse
from django.utils.dateparse import parse_datetime

from core import discussion_forms, discussion_pages, discussion_vote
from intevia.test_postgresql_backend import clock
from src.intevia.services import cq, cq_reads, discussion, discussion_posts, member_navigation
from tests import test_discussion_posts as post_support
from tests import test_law_acts as laws
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, signed_in
from tests.test_uc2_ballot_reads import open_board_vote
from tests.test_uc2_due_result import second_overdue_vote
from tests.test_uc2_pages import FIXTURE_HASHERS, path, seated_board

# The ruled words (capture Z, Q4), pinned apart from the production constant.
NO_ACTION = ("This vote had no action attached. INTEVIA recorded the Board's decision and carried out "
             "nothing further. INTEVIA does not move money.")
TITLE = "Door repair, private title"
FIRST_WORDING = "Approve one payment."
DECIDED_WORDING = "Approve the £84 repair to the community room door."


def facts(page):
    """The Decision card's rows, as {term: its dd node}.

    The closed vote's name rows are a second list of the same pattern (the posts box, part PB-5); they are not these.
    """
    rows, = [node for node in page.select("dl", classes=("office-facts",))
             if "vote-names" not in node["attrs"]["class"].split()]
    terms = [node["text"].strip() for node in page.select("dt", within=rows)]
    return dict(zip(terms, page.select("dd", within=rows), strict=True))


def row_time(page, row):
    """The instant a row's one <time> element carries."""
    stamp, = page.select("time", within=row)
    return parse_datetime(stamp["attrs"]["datetime"])


def minute(stamp):
    return stamp.astimezone(timezone.utc).replace(second=0, microsecond=0)


def notices(page):
    """INTEVIA's voice boxes inside the page's main area."""
    main, = page.select("main")
    return [node["text"].strip() for node in page.select("aside", within=main, classes=("notice",))]


def carried_payment():
    """A Board of three carries a plain Suggestion whose wording was edited once before its ballot.

    The route is ``tests.test_uc2_pages.test_board_result_occupies_its_recorded_post_number``'s:
    every act goes through the real services, and full turnout records the result at once.
    """
    owner, board, founder, coordinator, trustee = seated_board()
    support = post_support.DiscussionPostTests()
    args = support.board(owner, coordinator, board)
    topic = support.start(args, topic_type="SUGGESTION", title=TITLE, markdown=FIRST_WORDING)
    scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
    cq.put_forward(**scope, request_uuid=uuid4(), version_uuid=topic["version"])
    discussion_posts.edit_post(**args, thread_uuid=topic["thread"], post_uuid=topic["post"],
                               predecessor=topic["version"], request_uuid=uuid4(), markdown=DECIDED_WORDING)
    cq.mark_prepared(**scope, request_uuid=uuid4())
    ballot = cq_reads.vote_status(**scope)["ballot"]
    for voter in (founder, coordinator, trustee):
        cq.answer(**(scope | {"credential": voter.credential}), ballot_uuid=ballot, choice="QUALIFIED", request_uuid=uuid4())
    result = cq_reads.vote_status(**scope, ballot_uuid=ballot)["result_uuid"]
    result_url = reverse("discussion_result", args=(owner.organism_id, board.circle_uuid, topic["thread"], result))
    return owner, board, trustee, path(owner, board, topic["thread"]), result_url


def stored_result(thread):
    """The one recorded result of this Topic, read from the record itself."""
    row, = discussion._rows(
        """SELECT act_uuid, effective_at, recorded_at, (facts->>'closed_at')::timestamptz AS closed_at,
                  facts#>>'{count,kind}' AS kind, facts->'count' AS count
             FROM public.core_cqact WHERE thread_uuid=%s AND operation='SETTLE'""", [thread])
    return row


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class DueResultPageTests(TransactionTestCase):
    """B9-01 (plan row D12), on results recorded after their deadline, with the wake off."""

    def test_b9_01_result_page_shows_this_results_own_times_and_wording(self):
        """D12. The Topic's link opens this exact result: its times and its wording, nothing of another's."""
        case = open_board_vote(overdue=True)
        trustee = case.people[2]
        other = second_overdue_vote(case)
        # Advisor, box 9 part 2, finding 2: the recording falls two minutes after the close, so the live Topic
        # must name both times (C1-403); the formatter's same-minute branch stays with B9-05 and B9-06.
        clock.advance(timedelta(minutes=2))
        self.assertEqual(cq.catch_up(credential=trustee.credential), "Due work recorded.",
                         "The fixture needs both overdue votes recorded by one explicit catch-up.")
        mine, theirs = stored_result(case.thread), stored_result(other["thread_uuid"])
        self.assertNotEqual(mine["closed_at"], mine["effective_at"],
                            "The fixture needs a result whose voting closed before the result was recorded.")
        self.assertGreater(minute(mine["recorded_at"]), minute(mine["closed_at"]),
                           "The fixture needs the result recorded in a later minute than voting closed.")
        client = signed_in(trustee)
        topic = Markup(client.get(case.url).content.decode())
        # C1-403 on the real read: the Topic's line names the close of voting and then the recording, each its own time.
        closing = mine["closed_at"].astimezone(timezone.utc)
        recording = mine["recorded_at"].astimezone(timezone.utc)
        named = (f"Voting closed {closing.day} {closing:%B %Y, %H:%M} UTC"
                 f" · Result recorded {recording.day} {recording:%B %Y, %H:%M} UTC")
        vote_section, = topic.select("section", id="vote")
        # Part PB-6b: nobody answered, so the red card leads the section; the status line is the vote card's first line.
        danger, = topic.select("div", within=vote_section, id="vote-danger")
        self.assertEqual([p["text"] for p in topic.select("p", within=danger)],
                         ["Insufficient participation. No decision was reached.",
                          f"{mine['count']['participated']} of {mine['count']['eligible']} eligible Board members took part. "
                          f"{mine['count']['required']} {'was' if mine['count']['required'] == 1 else 'were'} required."],
                         "Insufficient participation must be marked on the red card, with this result's own counts.")
        vote_card, = topic.select("div", within=vote_section, classes=("card", "vote-card"))
        line = topic.select("p", within=vote_card)[0]["text"]
        self.assertEqual(line.strip(), f"Vote ended · {named}",
                         "The Topic's whole line must read Vote ended, then when voting closed, then the recording in another minute.")
        # PC1-02 (capture BW, BW1) on the real read: the Topic's result post shows the stored recorded time.
        result_post, = topic.select("article", id=f"post-{mine['act_uuid']}")
        self.assertTrue(topic.select("p", within=result_post, classes=("meta",))[0]["text"].strip().startswith("Result recorded "),
                        "The Topic's result post must name a no-decision result's time \"Result recorded\".")
        self.assertEqual(row_time(topic, result_post), mine["recorded_at"],
                         "The Topic's \"Result recorded\" must be the stored result's own recorded time.")
        expected = reverse("discussion_result", args=(case.owner.organism_id, case.board.circle_uuid,
                                                      case.thread, mine["act_uuid"]))
        self.assertEqual([a["attrs"]["href"] for a in topic.select("a") if a["text"].strip() == "View vote result"],
                         [expected], "The Topic must link to its own result and to no other.")
        response = client.get(expected)
        self.assertEqual(response.status_code, 200, "A Board member must be able to open the result from its link.")
        page = Markup(response.content.decode())
        rows = facts(page)
        # Capture AH, Q15 (the posts box, part PB-6a): nobody answered, so this result reached no decision.
        # Its time row reads "Result recorded", once, and there is no "Took effect" row.
        self.assertEqual(mine["kind"], "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION",
                         "The fixture's result must be one that reached no decision.")
        self.assertEqual(list(rows), ["Deciding body", "Outcome", "Result recorded", "Voting closed"],
                         "A result with no decision names its time \"Result recorded\", once, and has no \"Took effect\" row.")
        self.assertEqual(row_time(page, rows["Result recorded"]), mine["recorded_at"],
                         "\"Result recorded\" must be the stored result's own recorded time.")
        self.assertEqual(row_time(page, rows["Voting closed"]), mine["closed_at"],
                         "\"Voting closed\" must be the closing time stored with this result.")
        self.assertEqual([node["text"].strip() for node in page.select("div", classes=("card", "motion-wording"))],
                         ["Approve one payment."], "The page must show the wording this vote was held on, in its own card.")
        self.assertIn([node["text"].strip() for node in page.select("h2", id="result-wording")],
                      (["Wording decided"], ["Wording voted on"]),
                      "A wording never edited carries no version label (capture AC, Q6).")
        html = response.content.decode()
        self.assertNotIn("Approve a second payment.", html, "Another result's wording must not appear on this page.")
        self.assertNotIn(str(theirs["act_uuid"]), html, "Another result's identity must not appear on this page.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=True)
class CarriedPaymentPageTests(TransactionTestCase):
    """B9-02 and B9-03 (plan rows D13 and D15), on a carried plain decision."""

    def test_b9_02_result_only_member_reads_the_whole_result(self):
        """D13. An unseated member reads the released Board Topic and the full, independent result."""
        owner, board, trustee, topic_url, result_url = carried_payment()
        observer = signed_in(laws.add_member(owner))
        self.assertEqual(observer.get(topic_url).status_code, 200,
                         "Membership admits this reader to the ordinary Board Topic.")
        response = observer.get(result_url)
        self.assertEqual(response.status_code, 200, "A member admitted to the result must not be refused for want of the Topic.")
        assert_private(self, response)
        page = Markup(response.content.decode())
        rows = facts(page)
        self.assertTrue(rows["Deciding body"]["text"].strip().startswith("Board of Trustees"),
                        "The deciding body must be named from the result's own read.")
        # Capture AT2 (the posts box, part PB-6c): the Outcome row reads the outcome word only.
        self.assertEqual(rows["Outcome"]["text"].strip(), "Carried", "The outcome must be shown.")
        self.assertIsNotNone(row_time(page, rows["Took effect"]), "The effective time must be shown.")
        # Reviewer (Grok), box 9 step 2, 1.2: the unseated member reads every Decision row a Board member reads.
        board_rows = facts(Markup(signed_in(trustee).get(result_url).content.decode()))
        self.assertIn("Voting closed", rows, "A held vote's result must say when voting closed.")
        self.assertEqual({term: node["text"].strip() for term, node in rows.items()},
                         {term: node["text"].strip() for term, node in board_rows.items()},
                         "The unseated member must read the same Decision rows as a Board member, times included.")
        self.assertIn("Wording decided · Version 2", [node["text"].strip() for node in page.select("h2")],
                      "The decided version must be named.")
        self.assertEqual([node["text"].strip() for node in page.select("div", classes=("motion-wording",))],
                         [DECIDED_WORDING], "The decided wording must be shown, and only that version.")
        self.assertEqual([a["text"].strip() for a in page.select("a", href=topic_url)], [TITLE, "Back to Topic"],
                         "The released Topic's title and return control must point to exactly that Topic.")
        self.assertNotContains(response, FIRST_WORDING, msg_prefix="The result gives the decided wording.")
        # Preserve the independently admitted result when the Topic read itself is withheld.
        with mock.patch.object(discussion_posts, "read_thread", side_effect=discussion.DiscussionUnavailable):
            result_only = observer.get(result_url)
        self.assertContains(result_only, DECIDED_WORDING)
        for withheld in (TITLE, FIRST_WORDING, "Back to Topic", topic_url + '"'):
            self.assertNotContains(result_only, withheld, msg_prefix="A withheld Topic has no link or title on its result.")
        # Advisor, box 9 part 1: a signed-in person who is not a member of this organisation gets the private refusal.
        from tests.s015_0021_support import register_test_learner
        stranger = signed_in(register_test_learner(username="b9-result-stranger-" + uuid4().hex))
        refused = stranger.get(result_url)
        self.assertEqual(refused.status_code, 404, "A person outside the organisation must not open its result.")
        assert_private(self, refused)
        self.assertNotIn(DECIDED_WORDING, refused.content.decode(), "The refusal must not carry the decided wording.")

    def test_b9_03_plain_carried_decision_says_no_action_attached(self):
        """D15. A carried plain decision carries INTEVIA's no-action words, and the page offers nothing to send."""
        owner, board, trustee, topic_url, result_url = carried_payment()
        response = signed_in(trustee).get(result_url)
        self.assertEqual(response.status_code, 200, "A Board member must be able to open the result.")
        page = Markup(response.content.decode())
        self.assertEqual(notices(page), [NO_ACTION],
                         "A carried decision with no act attached must say so in INTEVIA's ruled words.")
        main, = page.select("main")
        self.assertEqual((page.select("form", within=main), page.select("button", within=main)), ([], []),
                         "The result page must hold no form and no command: INTEVIA records, it does not transfer.")
        # The posts box, PB-6c (capture AW): the page names the Topic by its title, linked, above the way back.
        self.assertEqual([a["text"].strip() for a in page.select("a", within=main, href=topic_url)],
                         ["Door repair, private title", "Back to Topic"],
                         "A reader who may open the Topic sees its title, linked, and keeps the way back to it.")


class ResultPageProjectionTests(SimpleTestCase):
    """B9-04 and B9-06: what the page makes of a result's record, with no database."""

    def page(self, **changes):
        org, circle, thread, result = (uuid4() for _ in range(4))
        address = reverse("discussion_result", args=(org, circle, thread, result))
        request = RequestFactory().get(address)
        request.user = AnonymousUser()
        request.resolver_match = resolve(address)
        stamp = datetime(2026, 11, 24, 11, 5, 20, tzinfo=timezone.utc)
        record = {"post_uuid": result, "ballot": str(uuid4()), "count": {"kind": "QUALIFIED"},
                  "markdown": "Carried. 3 of 3 eligible took part. 2 required.",
                  "effective_at": stamp, "recorded_at": stamp, "closed_at": "2026-11-24T09:00:00.305081+00:00",
                  "deciding_body": {"full_name": "Board of Trustees", "short_label": "Board"},
                  "wording": DECIDED_WORDING, "wording_version_number": 2, "carried_no_attached_act": True} | changes
        context = {"result": record, **discussion_pages._result_context(record)}
        return Markup(render_to_string("core/discussion_result.html", context, request=request))

    def test_b9_04_result_with_an_attached_act_does_not_claim_no_action(self):
        """D15's control. The no-action words appear only where the read says no act was attached."""
        self.assertEqual(notices(self.page(carried_no_attached_act=True)), [NO_ACTION],
                         "The control: a carried plain decision shows the words.")
        self.assertEqual(notices(self.page(carried_no_attached_act=False)), [],
                         "A result with an act attached must not be told it had none.")

    def test_b9_06_times_parse_the_stored_text_and_compare_by_minute(self):
        """Capture Z, Q2 and Q3. The stored closing time is text; it is parsed, shown in UTC and compared by minute."""
        apart = facts(self.page(effective_at=datetime(2026, 11, 24, 10, 30, tzinfo=timezone.utc)))
        self.assertEqual({term: node["text"].strip() for term, node in apart.items() if term not in ("Deciding body", "Outcome")},
                         {"Took effect": "24 November 2026, 10:30 UTC", "Voting closed": "24 November 2026, 09:00 UTC",
                          "Result recorded": "24 November 2026, 11:05 UTC"},
                         "With three different instants, each time is shown under its own name (Advisor, box 9 part 1).")
        together = facts(self.page(closed_at="2026-11-24T11:05:01+00:00",
                                   recorded_at=datetime(2026, 11, 24, 11, 5, 59, tzinfo=timezone.utc)))
        self.assertEqual(list(together), ["Deciding body", "Outcome", "Took effect", "Voting closed"],
                         "Within the same minute, \"Result recorded\" is left out even though the seconds differ.")
        offset = facts(self.page(closed_at="2026-11-24T10:00:00+01:00"))
        self.assertEqual(offset["Voting closed"]["text"].strip(), "24 November 2026, 09:00 UTC",
                         "A stored time with another offset must be shown as the same instant in UTC.")
        none = facts(self.page(ballot=None, wording=None, wording_version_number=None, carried_no_attached_act=False))
        self.assertEqual(list(none), ["Deciding body", "Outcome", "Took effect"],
                         "A result with no ballot had no voting to close, so it shows neither row.")


class TopicResultLineTests(SimpleTestCase):
    """B9-05 (C1-403; capture Z, Q2): the Topic's result line, with no database."""

    def line(self, **row):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        with mock.patch.object(discussion_vote.cq, "board_topic_vote", return_value=row):
            vote = discussion_vote.panel(request, uuid4(), uuid4(), uuid4())
        page = Markup(render_to_string("core/components/board_vote.html", {"vote": vote}))
        # The status line is the vote card's first line; a red card, where there is one, sits above it (part PB-6b).
        card, = page.select("div", classes=("card", "vote-card"))
        return page.select("p", within=card)[0]["text"]

    def test_b9_05_topic_line_names_closing_and_recording(self):
        """The line names when voting closed, and when the result was recorded only if that is another minute."""
        closed = datetime(2026, 11, 24, 9, 0, 30, tzinfo=timezone.utc)
        recorded = datetime(2026, 11, 24, 11, 5, 20, tzinfo=timezone.utc)
        held = dict(phase="RESULT", outcome="QUALIFIED", ballot_uuid=uuid4())
        self.assertEqual(self.line(**held, closed_at=closed, effective_at=recorded),
                         "Decided · Carried · Voting closed 24 November 2026, 09:00 UTC"
                         " · Result recorded 24 November 2026, 11:05 UTC",
                         "In different minutes, both times are named, each under its own event.")
        self.assertEqual(self.line(**held, closed_at=closed, effective_at=closed.replace(second=59)),
                         "Decided · Carried · Voting closed 24 November 2026, 09:00 UTC",
                         "Within the same minute, only the close of voting is named.")
        self.assertEqual(self.line(**held, closed_at="2026-11-24T10:00:30+01:00", effective_at=recorded),
                         "Decided · Carried · Voting closed 24 November 2026, 09:00 UTC"
                         " · Result recorded 24 November 2026, 11:05 UTC",
                         "A closing time given as stored text must be read as the same instant.")
        # Captures AH (Q19) and AQ1 (the posts box, part PB-6b): the red card above says the outcome, so the
        # status line names the times only.
        self.assertEqual(self.line(phase="RESULT", outcome="NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", ballot_uuid=uuid4(),
                                   closed_at=closed, effective_at=recorded),
                         "Vote ended · Voting closed 24 November 2026, 09:00 UTC · Result recorded 24 November 2026, 11:05 UTC",
                         "A vote that reached no decision names its times in the same way.")
        self.assertEqual(self.line(phase="RESULT", outcome="NO_DECISION_FEWER_THAN_THREE", ballot_uuid=uuid4(),
                                   closed_at=closed, effective_at=recorded),
                         "Vote ended · Fewer than three eligible members remained. No decision was reached."
                         " · Voting closed 24 November 2026, 09:00 UTC · Result recorded 24 November 2026, 11:05 UTC",
                         "Every other no-decision result keeps its outcome in the line (capture AQ4).")
        self.assertEqual(self.line(phase="WITHDRAWN", effective_at=recorded),
                         "Suggestion withdrawn · 24 November 2026, 11:05 UTC",
                         "A Suggestion that never reached a ballot keeps its one time.")


class StartTopicPageTests(SimpleTestCase):
    """B9-07 and B9-08 (HG capture AA, Q5): the Board's "Start a Topic" page has no office chooser."""

    OFFICE = {"office_definition_uuid": uuid4(), "label": "Treasurer"}

    def page(self, **changes):
        org, circle = uuid4(), uuid4()
        address = reverse("discussion_start_form", args=(org, circle))
        request = RequestFactory().get(address)
        request.user = AnonymousUser()
        request.resolver_match = resolve(address)
        context = {"board_org": org, "board_circle": circle, "category": None, "office": None,
                   "offices": [self.OFFICE], "types": [("TOPIC", "Topic"), ("SUGGESTION", "Suggestion")],
                   "categories": [{"category_uuid": uuid4(), "name": "General"}], "command": "test-command",
                   "entered": {}} | changes
        html = render_to_string("core/discussion_start.html", context, request=request)
        return html, Markup(html)

    def test_b9_07_start_page_offers_no_office_chooser(self):
        """Even when the Board has offices, the page is the plain form; an office start still shows its office."""
        html, page = self.page()
        main, = page.select("main")
        for gone in ("Start about an office", "Choose an office", "Continue with this office", "Start without an office"):
            self.assertNotIn(gone, html, "The office chooser was removed from the Board's page (capture AA).")
        self.assertEqual([form["attrs"].get("method") for form in page.select("form", within=main)], ["post"],
                         "The page must hold the one form that starts a Topic, and no other.")
        self.assertEqual(page.select("select", within=main, name="office"), [], "No office can be chosen on this page.")
        self.assertEqual([node["attrs"]["name"] for node in page.select("select", within=main)], ["category", "type"],
                         "The plain form asks for the category and the type.")
        html, page = self.page(office=self.OFFICE)
        main, = page.select("main")
        self.assertIn("About this office · Treasurer", [node["text"].strip() for node in page.select("p", within=main)],
                      "A Topic started from an office's page must still name that office.")
        self.assertEqual([node["attrs"]["value"] for node in page.select("input", within=main, name="office")],
                         [str(self.OFFICE["office_definition_uuid"])],
                         "A Topic started from an office's page must still carry that office.")

    def test_b9_08_start_read_builds_no_office_list_and_keeps_the_office_route(self):
        """The page's read no longer hands the template a list of offices, and still resolves a pinned office."""
        org, circle = uuid4(), uuid4()
        request = SimpleNamespace(user=AnonymousUser())
        directory = {"board_offices": [self.OFFICE], "organisation": None, "circles": []}
        listing = {"categories": [], "reads_without_board_place": False}
        with mock.patch.object(discussion_forms, "_board_listing", return_value=listing), \
                mock.patch.object(discussion_forms.member_navigation, "member_navigation", return_value=directory):
            plain = discussion_forms._start_context(request, org, circle, None, None)
            pinned = discussion_forms._start_context(request, org, circle, None, str(self.OFFICE["office_definition_uuid"]))
        self.assertNotIn("offices", plain, "The read must not build an office list for a chooser that is gone.")
        self.assertIsNone(plain["office"], "Without a pinned office the form is the plain one.")
        self.assertEqual(pinned["office"], self.OFFICE, "The start from an office's page must still resolve its office.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class UnpinnedBoardPagesTests(TransactionTestCase):
    """B9-09 (capture AA, Q5; Advisor, box 9 part 2, finding 1): pages that show no office build no office list."""

    def test_b9_09_unpinned_start_and_board_list_read_no_offices(self):
        """The plain start form and the Board's discussion list never read the Board's offices; the default read still does."""
        owner, board, founder, coordinator, trustee = seated_board()
        reader = signed_in(trustee)
        start = reverse("discussion_start_form", args=(owner.organism_id, board.circle_uuid))
        with mock.patch.object(member_navigation, "_board_offices", wraps=member_navigation._board_offices) as offices:
            form = reader.get(start)
            listing = reader.get(path(owner, board))
            self.assertEqual((form.status_code, listing.status_code), (200, 200), "Both pages must still open for a trustee.")
            self.assertEqual(offices.call_count, 0, "A page with no office pinned must not read the Board's offices.")
            member_navigation.member_navigation(credential=trustee.credential, organism_uuid=owner.organism_id)
            self.assertEqual(offices.call_count, 1, "The control: the default navigation read still reads the offices.")
        page = Markup(form.content.decode())
        self.assertEqual(len(page.select("form", method="post")), 1, "The plain start form must still be offered.")
        self.assertIn(path(owner, board), [a["attrs"].get("href") for a in page.select("a")],
                      "The start form's breadcrumb must still lead back to the Board's discussion.")


    def test_b9_10_unpinned_topic_and_result_pages_read_no_offices(self):
        """Reviewer (Grok), box 9 step 2, 3.5: an unpinned Topic and its result page also leave the offices unread."""
        owner, board, trustee, topic_url, result_url = carried_payment()
        reader = signed_in(trustee)
        with mock.patch.object(member_navigation, "_board_offices", wraps=member_navigation._board_offices) as offices:
            topic, result = reader.get(topic_url), reader.get(result_url)
            self.assertEqual((topic.status_code, result.status_code), (200, 200),
                             "Both pages must still open for a trustee.")
            self.assertEqual(offices.call_count, 0,
                             "A Topic with no office pinned, and its result page, must not read the Board's offices.")
