"""Scene 4. Board and Topic reads with the reader's real, current standing."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from core.models import Circle, CircleStateEvent, OrganismMembership
from src.intevia.services import circle_commands, cq, discussion, discussion_posts
from tests import committee_support, election_support
from tests import cq_authority_support as boards
from tests import test_discussion_posts as post_support
from tests import test_election_mandates as election_mandates
from tests import test_law_acts as laws
from tests.s015_0021_support import database_now
from tests.test_discussion_office_links import office_basis, record_office
from tests.test_f0_ui import Markup
from tests.test_office_record_services import fixture_membership_state
from tests.test_uc1_office_page import assert_private, signed_in


# These four HTTP witnesses use a bound session. Password work factors are outside their claims.
FIXTURE_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def activate_trustees(scope, board):
    cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": False}, request_uuid=uuid4())
    circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())


def seated_board():
    owner, scope, trustees, board = boards.ordinary(3, board=True)
    founder, coordinator, trustee = trustees
    activate_trustees(scope, board)
    boards.appoint_board_coordinator(owner, board, founder, coordinator)
    return owner, board, founder, coordinator, trustee


def path(owner, board, thread=None):
    kwargs = {"org": owner.organism_id, "circle": board.circle_uuid}
    if thread is None:
        return reverse("board_discussion", kwargs=kwargs)
    return reverse("discussion_topic", kwargs={**kwargs, "thread": thread})


def read_counts():
    return discussion._rows("""SELECT (SELECT count(*) FROM public.core_discboard) AS boards,
        (SELECT count(*) FROM public.core_disccategory) AS categories,
        (SELECT count(*) FROM public.core_discthread) AS threads,
        (SELECT count(*) FROM public.core_discpost) AS posts,
        (SELECT count(*) FROM public.core_livingorganismevent) AS events""")[0]


@override_settings(ALLOWED_HOSTS=["testserver"], INTEVIA_TIMED_EVENTS_WAKE=True)
class BoardVotePageTests(TransactionTestCase):
    def test_board_lists_only_visible_topics(self):
        owner, board, _, coordinator, trustee = seated_board()
        reader, steward = signed_in(trustee), signed_in(coordinator)
        index = path(owner, board)
        parent = reverse("circle_page", kwargs={"org": owner.organism_id, "circle": board.circle_uuid})
        support = post_support.DiscussionPostTests()
        with patch("src.intevia.services.timed_events.run") as wake:
            before = read_counts()
            self.assertEqual(reader.get(index).status_code, 404, "An unprovisioned Board must use the unavailable page.")
            self.assertNotContains(reader.get(parent), index, msg_prefix="An unprovisioned Board must have no discussion link.")
            self.assertEqual(read_counts(), before, "Reading the unprovisioned Board must leave every counted record unchanged.")
            args = support.board(owner, coordinator, board)
            empty = reader.get(index)
            self.assertContains(empty, "No Topics to show here.", msg_prefix="The newly provisioned Board must show an empty category.")
            self.assertContains(reader.get(parent), index, msg_prefix="A seated reader must be able to enter the released Board discussion.")
            first = support.start(args, title="Pinned public question")
            second = support.start(args, title="Ordinary public question")
            concealed = support.start(args, title="Withheld opening")
            support.moderate(args, first, "PIN")
            support.hide(args, concealed, True, as_coordinator=True)
            categories = discussion.board_categories(**support.read_args(args))["categories"]
            private = next(cat for cat in categories if cat["functional_code"] == "COORDINATION")
            private_topic = support.start(args | {"category_uuid": private["category_uuid"]}, title="Private coordination")
            before = read_counts()
            response = reader.get(index)
            assert_private(self, response)
            page = Markup(response.content.decode())
            card = next(c for c in page.select("article") if page.select("h3", within=c)[0]["text"] == "Discussion")
            self.assertEqual([n["text"] for n in page.select("p", within=card, classes=("meta",))], ["2 Topics"],
                             "The Discussion count must include the two released openings only.")
            topic_list, = page.select("ul", within=card, classes=("discussion-topics",))
            self.assertEqual([(a["text"], a["attrs"]["href"]) for a in page.select("a", within=topic_list)],
                             [("Pinned public question", path(owner, board, first["thread"])),
                              ("Ordinary public question", path(owner, board, second["thread"]))],
                             "The pinned Topic must precede the newer opening, each at its own URL.")
            for withheld in ("Coordination", "Withheld opening", "Private coordination", str(concealed["thread"]), str(private_topic["thread"])):
                self.assertNotContains(response, withheld, msg_prefix="The trustee's page must omit every withheld Topic fragment.")
            permitted = steward.get(index)
            self.assertContains(permitted, "Withheld opening", msg_prefix="The moderating Coordinator must see the hidden opening.")
            self.assertContains(permitted, "Private coordination", msg_prefix="The Coordinator must see the private category's Topic.")
            self.assertEqual(read_counts(), before, "Repeated reads with different grants must create no forum or lineage rows.")
            self.assertFalse(wake.called, "The Board index, its parent and the unavailable page must never wake due decisions.")

    def test_topic_permission_and_unavailable_reason(self):
        owner, board, founder, coordinator, trustee = seated_board()
        reader = signed_in(trustee)
        support = post_support.DiscussionPostTests()
        args = support.board(owner, coordinator, board)
        topic = support.start(args, title="Repair authority", markdown="First wording.")
        url = path(owner, board, topic["thread"])
        reply = support.reply(args, topic, markdown="Read this quotation.", quote_version=topic["version"])
        discussion_posts.edit_post(**args, thread_uuid=topic["thread"], post_uuid=topic["post"],
                                   predecessor=topic["version"], request_uuid=uuid4(), markdown="Second wording.")
        before = read_counts()
        with patch("src.intevia.services.timed_events.run") as wake:
            response = reader.get(url)
            self.assertEqual(response.status_code, 200, "The seated trustee must be able to read the open Topic.")
            assert_private(self, response)
            page = Markup(response.content.decode())
            self.assertEqual([n["text"] for n in page.select("div", classes=("post-text",))],
                             ["Second wording.", "Read this quotation."], "The opening and reply must show their current versions.")
            self.assertEqual([p["text"] for q in page.select("blockquote") for p in page.select("p", within=q)][-1:],
                             ["First wording."], "Editing the opening must leave the reply's quotation at its original version.")
            self.assertEqual(read_counts(), before, "Reading a Topic and its quote must create no forum or lineage rows.")
            self.assertFalse(wake.called, "Reading the Topic must never wake due decisions.")
        support.hide(args, reply, True, as_coordinator=True)
        concealed_post = reader.get(url)
        self.assertContains(concealed_post, "Post unavailable.", msg_prefix="The readable thread must keep a neutral concealed reply.")
        self.assertNotContains(concealed_post, "Read this quotation.", msg_prefix="A concealed reply's text must disappear.")
        self.assertNotContains(concealed_post, "First wording.", msg_prefix="A concealed reply must release no quotation.")
        category = uuid4()
        discussion.create_category(**support.read_args(args), category_uuid=category, name="Archived work", request_uuid=uuid4())
        custom = args | {"category_uuid": category}
        archived = support.start(custom, title="Archived but readable")
        prior = discussion.category_history(**custom)[-1]["version_uuid"]
        discussion.retire_category(**custom, predecessor=prior, request_uuid=uuid4())
        self.assertContains(reader.get(path(owner, board, archived["thread"])), "This category is retired.",
                            msg_prefix="A retired category keeps readable history and explains the retirement.")
        fixture_membership_state(owner, founder, trustee, OrganismMembership.State.SUSPENDED)
        self.assertContains(reader.get(url), "This category is open to you for reading.",
                            msg_prefix="A suspended trustee may read and must see the read-only explanation.")
        support.moderate(args, topic, "CLOSE")
        self.assertContains(reader.get(url), "This Topic is closed.", msg_prefix="A readable closed Topic must explain its closure.")
        support.hide(args, topic, True, as_coordinator=True)
        withheld = reader.get(url)
        self.assertEqual(withheld.status_code, 404, "A concealed opening must now give the same private status as missing.")
        self.assertNotIn(b"This Topic is closed.", withheld.content, "The private refusal must never disclose the hidden Topic's closure.")
        self.assertIn(b"You tried to open a discussion that you are not authorised to view, or that does not exist.",
                      withheld.content, "Concealment must give the neutral refusal (capture AI).")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_start_links_original_office_version(self):
        owner, scope, trustees, board = boards.ordinary(3, board=True)
        founder, _, trustee = trustees
        definition = office_basis(owner, founder)
        first_version = record_office(owner, founder, definition)
        pin = record_office(owner, founder, definition, prior=first_version)
        activate_trustees(scope, board)
        args = post_support.DiscussionPostTests().board(owner, trustee, board)
        office_url = reverse("office_page", args=(owner.organism_id, pin))
        reader = signed_in(trustee)
        office = Markup(reader.get(office_url).content.decode())
        links = [a["attrs"]["href"] for a in office.select("a") if a["text"].strip() == "Start a Topic about this office"]
        self.assertEqual(len(links), 1, "An admitted trustee must have one start link from this office.")
        action, data = command_form(reader.get(links[0]))
        self.assertEqual(data["type"], "TOPIC", "An office start must submit the fixed Topic type.")
        data.update(category=str(args["category_uuid"]), title="Who can approve a payment while the Treasurer's seat is vacant?",
                    markdown="The Treasurer’s seat is vacant.")
        first = reader.post(action, data)
        self.assertEqual(first.status_code, 303, "Starting the office Topic must redirect privately to its opening post.")
        assert_private(self, first)
        replay = reader.post(action, data)
        self.assertEqual((replay.status_code, replay.get("Location")), (303, first["Location"]),
                         "Identical starts must return the same Topic and opening post.")
        topic_url, anchor = first["Location"].split("#")
        current_page = Markup(reader.get(office_url).content.decode())
        self.assertEqual([a["text"].strip() for a in current_page.select("a", href=topic_url)], [data["title"]],
                         "The Topic started at the office must appear once on its independently read office page.")
        saved = reader.get(topic_url)
        self.assertContains(saved, "Office definition · Version 2", msg_prefix="The original office version must remain visible.")
        self.assertContains(saved, f'id="{anchor}"', msg_prefix="The redirect must name the opening post on the page.")
        self.assertEqual(discussion._rows("SELECT count(*) AS n FROM public.core_discpostact WHERE payload->>'thread'=%s",
                                          [topic_url.rstrip("/").split("/")[-1]])[0]["n"], 1,
                         "Replaying the opening must record just one post act.")
        fixture_membership_state(owner, founder, trustee, OrganismMembership.State.SUSPENDED)
        self.assertNotContains(reader.get(office_url), "Start a Topic about this office",
                               msg_prefix="A reader without a posting grant must receive no start command.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_reply_replay_creates_one_post(self):
        owner, scope, trustees, board = boards.ordinary(3, board=True)
        _, opener, trustee = trustees
        activate_trustees(scope, board)
        support = post_support.DiscussionPostTests()
        args = support.board(owner, opener, board)
        opening = support.start(args, markdown="Original payment question.")
        url = path(owner, board, opening["thread"])
        reader = signed_in(trustee)
        displayed = reader.get(url)
        action, data = command_form(displayed)
        quotes = Markup(displayed.content.decode()).select("option", value=str(opening["version"]))
        self.assertEqual(len(quotes), 1, "The form must offer the exact visible version as a quotation.")
        discussion_posts.edit_post(**args, thread_uuid=opening["thread"], post_uuid=opening["post"],
                                   predecessor=opening["version"], request_uuid=uuid4(), markdown="Revised question.")
        data.update(markdown="I have read the provision.", quote=str(opening["version"]))
        first = reader.post(action, data)
        self.assertEqual(first.status_code, 303, "The second trustee's reply must lead to the recorded post.")
        assert_private(self, first)
        duplicate = reader.post(action, data)
        self.assertEqual((duplicate.status_code, duplicate.get("Location")), (303, first["Location"]),
                         "An identical reply must return the same post and its original anchor.")
        posts = discussion_posts.read_thread(**support.read_args(args), thread_uuid=opening["thread"])["posts"]
        self.assertEqual([(row["post_number"], row["markdown"]) for row in posts],
                         [(1, "Revised question."), (2, data["markdown"])], "Only one reply may follow the edited opening.")
        self.assertEqual((posts[1]["author_id"], posts[1]["quoted_markdown"]), (trustee.pk, "Original payment question."),
                         "The reply must name the second trustee and quote the version shown before the edit.")
        self.assertEqual(first["Location"], f'{url}#post-{posts[1]["post_uuid"]}', "The redirect must identify that reply.")
        count = discussion._rows("SELECT count(*) AS n FROM public.core_discpostact WHERE payload->>'thread'=%s AND operation='REPLY'",
                                 [str(opening["thread"])])[0]["n"]
        self.assertEqual(count, 1, "Identical submissions must leave one reply receipt.")
        denied = signed_in(opener).post(action, data)
        self.assertEqual(denied.status_code, 400, "Another trustee cannot use the first reader's signed form.")
        _, fresh = command_form(reader.get(url))
        new = reader.post(action, fresh | {"markdown": "A separate reply.", "quote": ""})
        self.assertEqual(new.status_code, 303, "A fresh form must allow a deliberate second contribution.")
        self.assertNotEqual(new["Location"], first["Location"], "A new contribution must have its own post identity.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_revised_office_link_keeps_original_version(self):
        from src.intevia.services.organism_role_definition_service import DefinitionContent, DefinitionProvision

        # Main's C1-108-R route. The same three people can take separately elected seats on restoration.
        owner, founder = laws.ordinary_birth(offering_choice="FREE", board_seats=6, elected=(3, 4, 5), board_minimum=None)
        board_id = discussion._board_id(owner.organism_id, None)
        now = database_now()
        elections = {f"Seat {n}": election_support.seat(place=f"trustee-{n}", term={"end": (now + timedelta(days=n)).isoformat()})
                     for n in (3, 4, 5)}
        charter, version = committee_support.first_version(founder, {
            "board_makeup": {"minimum": 3}, "board_restoration": {"Restore": {"body": str(board_id)}},
            "committees": {"chair_board_member": False}, "elections": elections,
            "offices": {"Treasurer": "The Treasurer approves ordinary payments."},
        })
        proposal = laws.adoption(owner, charter, "ARTICLE")
        self.assertEqual(laws.law.OrganismLawService().adopt_law(laws.command(founder, proposal)).status, "ADOPT",
                         "The Board’s restoration and office provisions must be adopted before the first term.")
        content = DefinitionContent(code="TREASURER", provisions=(DefinitionProvision("OFFICE_BASIS", "Treasurer", str(version.pk)),))
        original = record_office(owner, founder, content)
        journey = election_mandates.ElectionMandateTests()
        journey.topic_category_ids = {}
        _, general, governed, trustees = journey.activate_board(owner, founder)
        board = Circle.objects.get(circle_uuid=governed["circle_uuid"])
        first = CircleStateEvent.objects.get(circle=board, action="ACTIVATE")
        args = post_support.DiscussionPostTests().board(owner, trustees[1], board)
        reader = signed_in(trustees[2])
        start = reverse("discussion_start_form", args=(owner.organism_id, board.circle_uuid))
        action, fields = command_form(reader.get(start, {"office": str(original)}))
        fields.update(category=str(args["category_uuid"]), title="Treasurer duties", markdown="First wording.")
        opening = reader.post(action, fields)
        self.assertEqual(opening.status_code, 303, "The first Board term must admit an opening pinned to office version 1.")

        stand_down = cq.stand_down_attachment(organism_uuid=owner.organism_id, circle_uuid=board.circle_uuid,
                                              lifecycle_uuid=first.event_uuid, board_uuid=board_id)
        down = committee_support.decide_instrument(journey, general, stand_down, trustees)["result"]
        board.refresh_from_db()
        self.assertNotEqual(board.state, "ACTIVE", "The Board must stand down before the members can turn voting off.")
        off = cq.settings_attachment(organism_uuid=owner.organism_id, predecessor=cq.settings(**general)["version"],
                                     changes={"enabled": False})
        committee_support.decide_instrument(journey, general, off, trustees)
        successor = record_office(owner, founder, content, prior=original)
        self.assertNotEqual(successor, original, "The amendment must create a successor definition.")
        cq.change_settings(**general, predecessor=cq.settings(**general)["version"], changes={"enabled": True}, request_uuid=uuid4())
        citation = {"binding_uuid": proposal.proposal, "version_id": version.pk}
        restore = cq.restoration_attachment(organism_uuid=owner.organism_id, circle_uuid=board.circle_uuid,
                                            stand_down_uuid=down, board_uuid=board_id,
                                            citation={"binding": str(proposal.proposal), "version": version.pk, "provision": "Restore"})
        restored = committee_support.decide_instrument(journey, general, restore, trustees)["result"]
        for n, trustee in zip((3, 4, 5), trustees, strict=True):
            seat = election_support.call(journey, general, citation, provision=f"Seat {n}", place=f"trustee-{n}")
            election_support.nominate(seat, trustee)
            ballot = election_support.open_election(seat)
            for voter in trustees:
                election_support.cast(seat, ballot, voter)
        circle_commands.activate(**general, circle_uuid=board.circle_uuid, restoration_result_uuid=restored, request_uuid=uuid4())
        board.refresh_from_db()
        self.assertEqual(board.state, "ACTIVE", "The original Board must be active on its elected seats.")
        response = reader.get(opening["Location"].split("#")[0])
        page = Markup(response.content.decode())
        destination = reverse("office_page", args=(owner.organism_id, successor))
        self.assertEqual([a["text"] for a in page.select("a", href=destination)], ["Treasurer"],
                         "A Topic pinned before amendment must link to the current definition of that same office.")
        self.assertContains(response, "Office definition · Version 1", msg_prefix="The link must preserve the original version label.")
        self.assertEqual(reader.get(destination).status_code, 200, "The released family link must open for the same reader.")
        replay = reader.post(action, fields)
        self.assertEqual((replay.status_code, replay.get("Location")), (303, opening["Location"]),
                         "A still-admitted retry after amendment must recognise the original receipt and retain its v1 pin.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_revoked_category_refused_on_form_and_submit(self):
        owner, board, _, coordinator, trustee = seated_board()
        support = post_support.DiscussionPostTests()
        args = support.read_args(support.board(owner, coordinator, board))
        category = uuid4()
        discussion.create_category(**args, category_uuid=category, name="Payments", request_uuid=uuid4())
        start = reverse("discussion_start_form", args=(owner.organism_id, board.circle_uuid))
        reader = signed_in(trustee)
        url = f"{start}?category={category}"
        action, fields = command_form(reader.get(url))
        fields.update(category=str(category), type="TOPIC", title="Payments question", markdown="My retained text.")
        scoped = args | {"category_uuid": category}
        predecessor = discussion.category_history(**scoped)[-1]["version_uuid"]
        discussion.retire_category(**scoped, predecessor=predecessor, request_uuid=uuid4())
        with patch.object(discussion_posts, "start_topic", wraps=discussion_posts.start_topic) as start_service:
            denied = reader.post(action, fields)
            self.assertFalse(start_service.called, "The submitted form must recheck the revoked category before calling START.")
            self.assertContains(denied, "This category has been retired", status_code=403,
                                msg_prefix="The reader must learn why the earlier form cannot submit.")
            fresh = reader.get(url)
            self.assertContains(fresh, "This category has been retired", status_code=403,
                                msg_prefix="A new form must also refuse the retired category.")
            self.assertNotContains(fresh, 'name="command"', status_code=403, msg_prefix="The denied category must release no form.")
        missing = reader.get(f"{start}?category={uuid4()}")
        self.assertEqual(missing.status_code, 404, "An unknown category must receive the private page.")
        hidden = uuid4()
        discussion.create_category(**args, category_uuid=hidden, name="Private review", request_uuid=uuid4())
        address = f"{start}?category={hidden}"
        hidden_action, hidden_fields = command_form(reader.get(address))
        scoped = args | {"category_uuid": hidden}
        prior = discussion.category_history(**scoped)[-1]["version_uuid"]
        discussion.revise_category(**scoped, predecessor=prior, name="Private review", description="",
                                   order=0, access="COORDINATORS", request_uuid=uuid4())
        with patch.object(discussion_posts, "start_topic") as start_service:
            sent = reader.post(hidden_action, hidden_fields | dict(category=str(hidden), type="TOPIC",
                                                                    title="Withdrawn access", markdown="Private draft."))
            for denied in (sent, reader.get(address)):
                self.assertEqual((denied.status_code, denied.content), (404, missing.content),
                                 "Loss of category visibility must use the same private page as a missing category.")
            self.assertFalse(start_service.called, "A concealed category must stop both form paths before the service.")


def command_form(response):
    """Find the actual discussion form and carry its own hidden fields."""
    page = Markup(response.content.decode())
    command, = page.select("input", name="command")
    form = next(parent for parent in command["parents"] if parent["tag"] == "form")
    return form["attrs"]["action"], {node["attrs"]["name"]: node["attrs"]["value"]
                                    for node in page.select("input", within=form, type="hidden")}
