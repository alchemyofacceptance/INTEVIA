"""C1-467 B. Read-only Board journeys and the Python boundary of the wider predicate."""
import ast
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core import discussion_forms
from core.models import OrganismMembership
from src.intevia.services import cq, cq_reads, discussion, discussion_posts
from tests.s015_0021_support import register_test_learner
from tests.test_board_discussion_audience import scope, topic_at
from tests.test_f0_ui import Markup
from tests.test_law_acts import add_member
from tests.test_office_record_services import fixture_membership_state
from tests.test_uc1_office_page import assert_private, signed_in
from tests.test_uc2_pages import FIXTURE_HASHERS, path, read_counts, seated_board


BOARD_LINE = "You can read this Board discussion. Only Board members can contribute."


def source_owner(node, parents):
    """Identify a named function or the module assignment that contains this SQL."""
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
        if isinstance(node, ast.Assign) and isinstance(parents[node], ast.Module):
            return ast.unparse(node.targets[0])
    return "<module>"


class BoardDiscussionReadBoundaryTests(SimpleTestCase):
    def test_only_named_python_reads_use_the_wider_audience(self):
        """Inventory first-party Python, including commands, independent of Git or a live database."""
        root = Path(__file__).resolve().parents[1]
        predicate = re.compile(r"\bdisc_read_audience\b", re.I)
        readers, keywords = [], []
        # Scan new first-party packages as well as the existing app. Skip dependencies,
        # archived copies, test harnesses and migration bodies covered by catalogue check (i).
        sources = []
        for parent, dirs, names in os.walk(root):
            directory = Path(parent)
            dirs[:] = [name for name in dirs if name not in {
                ".git", ".tox", "__pycache__", "node_modules", "migrations",
            } and not (directory / name / "pyvenv.cfg").is_file()]
            if directory == root:
                dirs[:] = [name for name in dirs if name not in {"tests", "archive", "INTEVIA_BACKUP"}]
            sources.extend(directory / name for name in names if name.endswith(".py"))
        for source in sources:
            text = source.read_text(encoding="utf-8-sig")
            if not predicate.search(text) and "discussion_read" not in text:
                continue
            tree = ast.parse(text)
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for _ in predicate.finditer(node.value):
                        readers.append((source.relative_to(root).as_posix(), source_owner(node, parents)))
                if isinstance(node, ast.keyword) and node.arg == "discussion_read":
                    keywords.append((source.relative_to(root).as_posix(), source_owner(node, parents)))
        services = "src/intevia/services/"
        self.assertCountEqual(readers, [
            (services + "discussion.py", "_READABLE_BOARDS"),  # MEMBERS
            (services + "discussion.py", "_READABLE_BOARDS"),  # PROBATIONARY
            (services + "discussion.py", "board_categories"),
            (services + "discussion.py", "category_history"),
            (services + "discussion_posts.py", "visible_boards"),
            (services + "discussion_posts.py", "_category"),
            (services + "discussion_posts.py", "topics_about_office"),
            (services + "cq_reads.py", "result_scope_query"),
        ], "A new reader of the broader predicate requires its own reason and review.")
        self.assertCountEqual(keywords, [(services + "cq.py", "board_topic_vote"),
                                        (services + "cq_reads.py", "_scope")],
                              "Only the Topic panel opts in; _scope forwards the caller's choice.")

    def test_read_only_words_follow_the_board_flag_on_both_pages(self):
        """The Board line appears once even for an empty index, and replaces the old line on a Topic."""
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        org, circle = uuid4(), uuid4()
        category = {"name": "Discussion", "retired": False, "can_reply": False, "can_start_topic": False}
        context = {"board_org": org, "board_circle": circle, "reads_without_board_place": True,
                   "organisation": {"organism_uuid": org}, "circle": {"circle_uuid": circle},
                   "categories": [], "category": category, "topic": {"title": "Door repair", "topic_type": "TOPIC"},
                   "posts": [{"post_number": 1, "markdown": "A repair.", "version_number": 1}]}
        index = render_to_string("core/board_discussion.html", context, request=request)
        self.assertEqual(index.count(BOARD_LINE), 1, "The index states the Board-place rule once, outside its category loop.")
        detail = render_to_string("core/discussion_topic.html", context, request=request)
        self.assertEqual(detail.count(BOARD_LINE), 1, "The Topic must explain the unseated reader's standing once.")
        self.assertNotIn("This category is open to you for reading.", detail)
        self.assertNotIn("Start a Topic", index)

    def test_no_private_detail_read_after_discussion_only_admission(self):
        """Isolate the early exit inside the transaction, after the two authenticated scope reads."""
        actor, org, board, thread = SimpleNamespace(pk=17), uuid4(), uuid4(), uuid4()
        with patch.object(discussion, "_actor", return_value=actor), \
                patch.object(cq_reads, "_scope", return_value={"thread_audience": False}) as scope_read, \
                patch.object(discussion, "_rows", side_effect=AssertionError("A private vote read followed admission.")):
            result = cq.board_topic_vote.__wrapped__(credential=object(), organism_uuid=org,
                                                     circle_uuid=board, thread_uuid=thread)
        scope_read.assert_called_once_with(actor, org, board, thread, discussion_read=True)
        self.assertIsNone(result, "Discussion-only admission must end the read before vote details or command checks.")

    def test_scope_keyword_changes_only_the_outer_read(self):
        """The default query, private result column and all seven bound arguments retain their scope."""
        args = (SimpleNamespace(pk=17), uuid4(), uuid4(), uuid4())
        narrow, bindings = cq_reads.result_scope_query(*args)
        wide, other_bindings = cq_reads.result_scope_query(*args, discussion_read=True)
        before = "AND (public.disc_audience("
        self.assertEqual(narrow.count(before), 1)
        self.assertNotIn("disc_read_audience", narrow)
        self.assertEqual(wide, narrow.replace(before, "AND (public.disc_read_audience("))
        self.assertEqual(bindings, [17, *args[1:], 17, 17, 17])
        self.assertEqual(other_bindings, bindings)


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class BoardDiscussionReaderPageTests(TransactionTestCase):
    def test_board_welcome_posting_flags_require_a_seat(self):
        """FLAG. Old standing alone admits this real Board Welcome reader."""
        owner, board, founder, coordinator, _ = seated_board()
        member = add_member(owner)
        fixture_membership_state(owner, founder, member, OrganismMembership.State.PROBATIONARY)
        discussion.set_probation_access(credential=founder.credential, organism_uuid=owner.organism_id,
                                         enabled=True, request_uuid=uuid4())
        discussion.open_circle_welcome(**scope(owner, coordinator, board), request_uuid=uuid4())
        listing = discussion.board_categories(**scope(owner, member, board), governance_only=True)
        welcome, = (row for row in listing["categories"] if row["functional_code"] == "WELCOME")
        self.assertTrue(listing["reads_without_board_place"], "This member must read Welcome without a Board place.")
        self.assertEqual((welcome["can_reply"], welcome["can_start_topic"]), (False, False),
                         "A readable Welcome category must offer neither command to an unseated probationary member.")

    def test_member_reads_only_the_released_category_history(self):
        owner, board, _, coordinator, _ = seated_board()
        member = scope(owner, add_member(owner), board)
        officer = scope(owner, coordinator, board)
        categories = {r["functional_code"]: r for r in discussion.board_categories(**officer)["categories"]}
        public = categories["DISCUSSION"]["category_uuid"]
        private = categories["COORDINATION"]["category_uuid"]
        expected = discussion.category_history(**officer, category_uuid=public)
        try:
            history = discussion.category_history(**member, category_uuid=public)
        except discussion.DiscussionUnavailable:
            history = []
        self.assertTrue(history, "The member must receive the actual released category history.")
        self.assertEqual([r["version_uuid"] for r in history], [r["version_uuid"] for r in expected])
        with self.assertRaises(discussion.DiscussionUnavailable):
            discussion.category_history(**member, category_uuid=private)

    def test_seated_restricted_reader_keeps_the_category_line(self):
        owner, board, founder, coordinator, trustee = seated_board()
        _, _, topic = topic_at(owner, board, coordinator)
        fixture_membership_state(owner, founder, trustee, OrganismMembership.State.RESTRICTED)
        reader = signed_in(trustee)
        response = reader.get(path(owner, board, topic["thread"]))
        self.assertContains(response, "This category is open to you for reading.", count=1)
        self.assertNotContains(response, BOARD_LINE)
        self.assertNotContains(response, 'name="command"')
        self.assertNotContains(reader.get(path(owner, board)), BOARD_LINE)

    def test_forced_contributions_have_their_own_five_part_refusals(self):
        """Send correctly bound forms to both real endpoints; the read alone cannot record either command."""
        owner, board, _, coordinator, _ = seated_board()
        member = signed_in(add_member(owner))
        _, category, topic = topic_at(owner, board, coordinator)
        thread = topic["thread"]
        org, circle = owner.organism_id, board.circle_uuid
        page = member.get(path(owner, board, thread))
        self.assertEqual(page.status_code, 200)
        before = read_counts()
        for operation, noun, reason, target in (
            ("reply", "reply", "Only Board members can reply here.", thread),
            ("start", "Topic", "Only Board members can start a Topic here.", None),
        ):
            with self.subTest(operation=operation):
                args = (org, circle, thread) if target else (org, circle)
                token = discussion_forms.issue_form(page.wsgi_request, operation, org, circle, thread=target)
                response = member.post(reverse("discussion_" + operation, args=args), {
                    "command": token, "markdown": "A second opinion.", "category": str(category),
                    "title": "Door repair", "type": "TOPIC",
                })
                self.assertEqual(response.status_code, 403)
                assert_private(self, response)
                markup = Markup(response.content.decode())
                card, = markup.select("div", id="discussion-error")
                self.assertEqual([n["text"] for n in markup.select("h1")], [f"Your {noun} could not be recorded."])
                self.assertEqual([n["text"] for n in markup.select("p", within=card)], [
                    reason, "This request recorded no new contribution.",
                    "Reopen the page and check the current wording and available actions.",
                    "If you need help, contact a Board Coordinator.",
                ], "The authority refusal must preserve the effect, next action and nearest help.")
                self.assertNotContains(response, 'name="command"', status_code=403)
        start_url = reverse("discussion_start_form", args=(org, circle))
        for address in (start_url, f"{start_url}?category={category}"):
            self.assertContains(member.get(address), "Only Board members can start a Topic here.", status_code=403)
        self.assertEqual(read_counts(), before, "The forced submissions and form previews must leave no records.")

    def test_hidden_suggestion_and_foreign_reader_keep_private_404(self):
        owner, board, _, _, trustee = seated_board()
        _, category, topic = topic_at(owner, board, trustee, kind="SUGGESTION")
        thread = topic["thread"]
        address = path(owner, board, thread)
        member = signed_in(add_member(owner))
        stranger = signed_in(register_test_learner(username="c467-foreign-" + uuid4().hex))
        absent = path(owner, board, uuid4())
        self.assertContains(member.get(address), "Discuss the repair.")
        outside = stranger.get(address)
        self.assertEqual((outside.status_code, outside.content), (404, stranger.get(absent).content))
        assert_private(self, outside)
        discussion_posts.set_post_hidden(**scope(owner, trustee, board), category_uuid=category,
                                         thread_uuid=thread, post_uuid=topic["post"], request_uuid=uuid4(), hidden=True)
        hidden = member.get(address)
        self.assertEqual((hidden.status_code, hidden.content), (404, member.get(absent).content),
                         "A hidden Suggestion must keep the private refusal even for an admitted Board reader.")
        assert_private(self, hidden)
        self.assertNotIn(b"Discuss the repair.", hidden.content)
