"""14b. Reopen independently granted Board results from a human-readable record."""

from unittest.mock import patch
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from core import law_pages, models
from src.intevia.services import care_assistance, circle_commands, cq, cq_reads, discussion, discussion_posts, library_policy
from src.intevia.services.law_acts import LawProposal
from tests import cq_authority_support as boards
from tests import cq_settings_support as ballots
from tests import test_cq_clarity as clarity
from tests.office_record_support import a1_adopt_policy, a1_definition
from tests.test_discussion_office_links import record_office
from tests.test_f0_ui import Markup
from tests.test_law_acts import add_member, ordinary_birth
from tests.test_uc1_office_page import office_path, signed_in
from tests.test_uc2_pages import FIXTURE_HASHERS, activate_trustees


def links(response, *, label=None):
    """Read links from the requested landmark, retaining their actual destinations."""
    page = Markup(response.content.decode())
    return [a["attrs"]["href"] for a in page.select("a")
            if label is None or a["text"].strip() == label]


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class PolicyResultLinksTests(TransactionTestCase):
    """Exercise the public page routes against distinct, committed Board results."""

    def set_board(self, *, office=False):
        """Use a real three-seat Board with Clarity disabled for these result-only cases."""
        if office:
            self.owner, founder = ordinary_birth(offering_choice="COMPLIMENTARY_BETA", board_seats=3)
            self.scope = dict(credential=founder.credential, organism_uuid=self.owner.organism_id)
            self.board = models.Circle.objects.get(parent_organism=self.owner, kind="GOVERNANCE")
            self.trustees = [founder, *(boards.seat_trustee(self.owner, founder, self.board, i) for i in (1, 2))]
        else:
            self.owner, self.scope, self.trustees, self.board = boards.ordinary(3, board=True)
        self.org = self.owner.organism_id
        founder, proposer = self.trustees[:2]
        if office:
            self.ref = "approval-" + uuid4().hex
            self.first, self.origin, receipt = a1_adopt_policy(self.owner, founder, self.ref, "[#approval] Original duties.")
            self.assertEqual(receipt.status, "ADOPT")
            self.office = record_office(self.owner, founder, a1_definition(self.ref, 1))
            care_assistance.set_assistance(**self.scope, enabled=False, predecessor=None, request_uuid=uuid4())
            cq.change_settings(**self.scope, predecessor=None, request_uuid=uuid4(),
                               changes={"enabled": True, "clarity_stage": False, "reporting": "TOTALS_ONLY"})
            circle_commands.activate(**self.scope, circle_uuid=self.board.circle_uuid, request_uuid=uuid4())
        else:
            activate_trustees(self.scope, self.board)
        boards.appoint_board_coordinator(self.owner, self.board, founder, proposer)
        self.local = self.scope | dict(credential=proposer.credential, circle_uuid=self.board.circle_uuid)
        self.topic_category_ids = {}

    def vote(self, wording, attachment=None):
        """Settle one real vote and return its Topic and exact result coordinates."""
        journey, _, post = clarity.CQClarityTests.topic(self, self.local, title=wording, markdown=wording)
        cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=post["version"], attachment=attachment)
        cq.mark_prepared(**journey, request_uuid=uuid4())
        ballot = cq_reads.vote_status(**journey)["ballot"]
        result = ballots.settle(journey, ballot, self.trustees)
        self.assertEqual(result["count"]["kind"], "QUALIFIED")
        if attachment:
            self.assertEqual(result["disposition"], "APPLIED")
        coords = (self.org, self.board.circle_uuid, journey["thread_uuid"], result["post_uuid"])
        return reverse("discussion_topic", args=coords[:3]), reverse("discussion_result", args=coords), result

    def test_remy_reopens_adoption(self):
        """L06/A6. A fresh unseated member traces current and earlier law, with CARE off."""
        self.set_board(office=True)
        founder = self.trustees[0]
        payment = self.vote("Approve the £84 door repair.")
        clause = "Two trustees may approve an ordinary payment during a vacancy."
        content = f"[#approval] {clause}\n[#payment-result] {payment[2]['post_uuid']}"
        draft = library_policy.draft_policy(**self.scope, resource_reference=self.ref, content=content,
            authors=[founder.identity_id], declare_own_authorship=True, predecessor_version_id=self.first.pk,
            request_uuid=uuid4())
        spec = dict(organism=str(self.org), resource=self.ref, version=2)
        publication = self.vote("Publish the revised duties.", dict(kind="PUBLISH_POLICY", spec=spec, text=content))
        proposal = LawProposal(organism=self.org, resource=self.ref, proposal=uuid4(), predecessor=self.origin,
                               operation="ADOPT", intent="ADOPT", law_class="POLICY", version=2)
        adoption = self.vote("Adopt the revised duties.", dict(kind="LAW", spec=proposal.content(), text=content))
        current = reverse("organisation_law_version", args=(self.org, proposal.proposal, 2))
        old = reverse("organisation_law_version", args=(self.org, self.origin, 1))
        home_url = office_path(self.owner, self.office)
        reader = signed_in(add_member(self.owner))
        home = reader.get(home_url)
        self.assertEqual(home.status_code, 200)
        self.assertFalse(home.context["record"]["care"]["enabled"])
        self.assertNotContains(home, 'aria-label="CARE observation"')
        self.assertIn(current, links(home))
        self.assertIn(old, links(reader.get(current)))
        first = reader.get(old)
        self.assertIn(current, links(first, label="Current version: Version 2"))
        self.assertEqual(links(first, label="View vote result"), [])
        exact = [v[1] for v in (payment, publication, adoption)]
        self.assertEqual(links(reader.get(current), label="View vote result"), exact, "L06 exact three results")
        page = Markup(home.content.decode())
        history, = page.select("section", **{"aria-labelledby": "office-history"})
        self.assertEqual({a["attrs"]["href"] for a in page.select("a", within=history)
                          if a["text"].strip() == "View vote result"}, set(exact), "L06 three history destinations")
        cards = page.select("article", classes=("office-decision",))
        self.assertEqual([a["attrs"]["href"] for card in cards for a in page.select("a", within=card)],
                         [adoption[1], payment[1]])
        for topic, url, record in (payment, publication, adoption):
            response = reader.get(url)
            self.assertEqual(response.context["result"]["post_uuid"], record["post_uuid"])
            self.assertIsNone(response.context["result"]["own_answer"])
            self.assertIsNone(response.context["result"]["tally"]["names"])
            self.assertNotContains(response, "vote-names")
            self.assertNotContains(response, clause)
            self.assertIn(topic, links(response, label="Back to Topic"))
            self.assertIn(url, links(reader.get(topic), label="View vote result"))
        with patch.object(discussion_posts, "read_thread", side_effect=discussion.DiscussionUnavailable()):
            direct = reader.get(adoption[1])
            self.assertContains(direct, "Version 2 is in force.")
            self.assertNotContains(direct, "Back to Topic")
        real_result = cq_reads.result_post
        def withhold_adoption(**kwargs):
            """Withdraw one destination while the same reader keeps two independent grants."""
            if kwargs["result_uuid"] == adoption[2]["post_uuid"]:
                raise discussion.DiscussionUnavailable()
            return real_result(**kwargs)
        with patch.object(cq_reads, "result_post", side_effect=withhold_adoption):
            withheld = reader.get(home_url)
            self.assertNotContains(withheld, str(adoption[2]["post_uuid"]))
            self.assertEqual(set(links(withheld, label="View vote result")), set(exact[:2]))

    def test_publication_belongs_to_its_version(self):
        """L07. On one Policy, each adopted version has exactly its own Board publication and date."""
        self.set_board()
        founder = self.trustees[0]
        ref = "two-publications-" + uuid4().hex
        prior = binding = None
        versions = []
        for number in (1, 2):
            content = f"[#approval] Text reserved to version {number}."
            draft = library_policy.draft_policy(**self.scope, resource_reference=ref, content=content,
                authors=[founder.identity_id], declare_own_authorship=True, predecessor_version_id=prior,
                request_uuid=uuid4())
            spec = dict(organism=str(self.org), resource=ref, version=number)
            topic, pub_url, published = self.vote(f"Publish revision {number}",
                                                  dict(kind="PUBLISH_POLICY", spec=spec, text=content))
            proposal = LawProposal(organism=self.org, resource=ref, proposal=uuid4(), predecessor=binding,
                                   operation="ADOPT", intent="ADOPT", law_class="POLICY", version=number)
            _, adoption_url, _ = self.vote(f"Adopt revision {number}", dict(kind="LAW", spec=proposal.content(), text=content))
            prior, binding = draft.pk, proposal.proposal
            stamp, = discussion._rows("SELECT effective_at FROM public.core_libpublicationfact WHERE operation_uuid=%s",
                                       [published["post_uuid"]])
            versions.append((reverse("organisation_law_version", args=(self.org, binding, number)),
                             topic, pub_url, adoption_url, stamp["effective_at"], content))
        reader = signed_in(add_member(self.owner))
        for number, (url, topic, pub, adopted, at, content) in enumerate(versions, 1):
            with self.subTest(version=number):
                page = reader.get(url)
                self.assertEqual(links(page, label="View vote result"), [pub, adopted], "L07 exact publication and adoption")
                self.assertContains(page, f"The Board published version {number} on")
                self.assertEqual(page.context["publication"]["at"], at)
                self.assertIn(pub, links(reader.get(topic), label="View vote result"))
                result = reader.get(pub)
                self.assertContains(result, f"Version {number} was published.")
                self.assertNotContains(result, content.split("] ")[1])
                self.assertIsNone(result.context["result"]["own_answer"])
                with patch.object(cq_reads, "result_post", side_effect=discussion.DiscussionUnavailable()):
                    withheld = reader.get(url)
                    self.assertContains(withheld, f"The Board published version {number} on")
                    self.assertEqual(links(withheld, label="View vote result"), [])
                    self.assertNotContains(withheld, pub.rstrip("/").rsplit("/", 1)[-1])

    def test_plain_payment_destination(self):
        """L12-payment. The payment clause, Topic and result use the same plain vote and independent grant."""
        self.set_board()
        wording = "Approve the £84 door repair."
        topic, result_url, result = self.vote(wording)
        reader = add_member(self.owner)
        client = signed_in(reader)
        clause = "[#payment-result] " + str(result["post_uuid"])
        parts = law_pages.law_display_parts(reader.credential, self.org, clause)
        html = render_to_string("core/components/law_text.html", {"parts": parts})
        self.assertInHTML(f'<a href="{result_url}">View vote result</a>', html)
        self.assertIn(result_url, links(client.get(topic), label="View vote result"))
        page = client.get(result_url)
        self.assertContains(page, wording)
        self.assertIsNone(page.context["result"]["own_answer"])
        with patch.object(cq_reads, "result_post", side_effect=discussion.DiscussionUnavailable()):
            parts = law_pages.law_display_parts(reader.credential, self.org, clause)
            withheld = render_to_string("core/components/law_text.html", {"parts": parts})
            self.assertNotIn(str(result["post_uuid"]), withheld)
            self.assertNotIn("View vote result", withheld)
            topic_page = client.get(topic)
            self.assertNotContains(topic_page, str(result["post_uuid"]), msg_prefix="L12-Topic denied result identity")
            self.assertEqual(links(topic_page, label="View vote result"), [])
            self.assertEqual(client.get(result_url).status_code, 404)
