"""14a. Adopt only an exact recorded publication through a Board Suggestion."""

from contextlib import ExitStack, nullcontext
from unittest.mock import patch
from uuid import UUID, uuid4

from django.core import signing
from django.db import DatabaseError, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core import discussion_forms, discussion_pages, discussion_vote
from src.intevia.services import cq, cq_reads, discussion, library_policy, policy_adoption, policy_publication
from src.intevia.services.law_acts import LawProposal
from tests import cq_authority_support as boards
from tests import test_cq_clarity as clarity
from tests import test_law_acts as laws
from tests.office_record_support import a1_adopt_policy
from tests.test_f0_ui import Markup
from tests.test_policy_vote_pages import commands, confirmed
from tests.test_uc1_office_page import signed_in
from tests.test_uc23_shell import projected_forms
from tests.test_uc2_pages import FIXTURE_HASHERS, activate_trustees, command_form, path, preparation_receipts


MOTION = "Put the revised Treasurer duties into force."
CLAUSE = "Two trustees check a payment during a vacancy."
TEXT = "# Treasurer duties\n[#approval] " + CLAUSE + "\n[#record] Keep the signed approval."


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class PolicyAdoptionTests(TransactionTestCase):
    """Use real law versions, membership, Board commands and the result read."""

    def board_policy(self, *, publish=True, clarity_stage=False):
        """Bind v1, draft v2 and optionally publish it before activating the three-seat Board."""
        self.owner, scope, self.trustees, self.board = boards.ordinary(3, board=True)
        self.org = self.owner.organism_id
        founder, proposer, chair = self.trustees
        self.resource = "vacancy-policy-" + uuid4().hex
        v1, self.binding, receipt = a1_adopt_policy(self.owner, founder, self.resource,
                                                   "# Treasurer duties\n[#approval] The Treasurer checks.")
        self.assertEqual(receipt.status, "ADOPT")
        self.v2 = library_policy.draft_policy(**scope, resource_reference=self.resource, content=TEXT,
            authors=[founder.identity_id], declare_own_authorship=True, predecessor_version_id=v1.pk, request_uuid=uuid4())
        if publish:
            library_policy.publish_policy(**scope, version_id=self.v2.pk, request_uuid=uuid4())
        if clarity_stage:
            boards.appoint_board_chairs(self.owner, founder, chair)
        activate_trustees(scope, self.board, clarity=clarity_stage)
        boards.appoint_board_coordinator(self.owner, self.board, founder, proposer)
        self.board_scope = scope | {"credential": proposer.credential, "circle_uuid": self.board.circle_uuid}
        self.author = signed_in(proposer)
        self.topic_category_ids = {}

    def topic(self):
        """Start a separate genuine Suggestion on the same Board."""
        journey, _, post = clarity.CQClarityTests.topic(self, self.board_scope, markdown=MOTION)
        return journey, post["version"], path(self.owner, self.board, journey["thread_uuid"])

    def instrument(self, proposal):
        """Build an independently expected attachment using the law writer's public contract."""
        law = LawProposal(organism=self.org, resource=self.resource, proposal=proposal, version=2,
                          predecessor=self.binding, law_class="POLICY", operation="ADOPT", intent="ADOPT")
        return dict(kind="LAW", spec=law.content(), text=TEXT)

    def review(self, url):
        """Open the actual selected adoption form and return the signed confirmation."""
        endpoint, csrf, options = commands(self.author.get(url))
        choice, = [opt for opt in options if "Adopt a published Policy version" in opt["text"]]
        fields = dict(csrfmiddlewaretoken=csrf, vote_command=choice["attrs"]["value"])
        review = self.author.post(endpoint, fields)
        self.assertContains(review, "Put adoption to a Board vote")
        return endpoint, fields, review, confirmed(review)

    def answers(self, journey, ballot, choices):
        """Cast the listed answers in trustee order, with the third trustee settling last."""
        for member, answer in zip(self.trustees, choices):
            cq.answer(**(journey | {"credential": member.credential}), ballot_uuid=ballot,
                      choice=answer, request_uuid=uuid4())

    def test_adoption_requires_publication(self):
        """L09-unpublished. The ready draft has its publication choice only; a browser cannot relabel it."""
        self.board_policy(publish=False)
        journey, _, url = self.topic()
        page = self.author.get(url)
        self.assertNotContains(page, "Adopt a published Policy version")
        endpoint, csrf, options = commands(page)
        self.assertEqual(len(options), 2)
        draft = options[1]
        self.assertIn("Publish a Policy version", draft["text"])
        response = self.author.post(endpoint + "?intent=adopt", dict(
            vote_command=draft["attrs"]["value"], csrfmiddlewaretoken=csrf, intent="adopt", predecessor=str(uuid4())))
        self.assertContains(response, "Review publication")
        tampered = self.author.post(endpoint, dict(csrfmiddlewaretoken=csrf, vote_command=draft["attrs"]["value"] + "x"))
        self.assertEqual(tampered.status_code, 400)
        # Even a correctly scoped signed review cannot turn an unpublished target into a choice.
        issued = signing.loads(draft["attrs"]["value"], salt=discussion_forms.FORM_SALT)
        issued["confirmation"].update(intent="adopt", predecessor=str(self.binding))
        stale = self.author.post(endpoint, dict(csrfmiddlewaretoken=csrf,
            vote_command=signing.dumps(issued, salt=discussion_forms.FORM_SALT)))
        self.assertContains(stale, "The adoption could not be put to a vote.", status_code=409)
        self.assertNotContains(stale, CLAUSE, status_code=409)
        self.assertEqual(preparation_receipts(journey["thread_uuid"]), [])
        self.assertFalse(discussion._rows("SELECT 1 FROM public.core_cqact WHERE thread_uuid=%s", [journey["thread_uuid"]]))

    def test_adoption_preserves_reviewed_binding(self):
        """L09. Two reviews against v1: one exact initiation and replay, then a stale refusal at both boundaries."""
        self.board_policy()
        stale, opening, url = self.topic()
        stale_endpoint, stale_fields, _, stale_token = self.review(url)
        live, _, live_url = self.topic()
        endpoint, fields, review, token = self.review(live_url)
        self.assertContains(review, "This organisation")
        self.assertContains(review, str(reverse("organisation_law_version", args=(self.org, self.binding, 1))))
        self.assertContains(review, "Proposed for adoption · Version 2")
        self.assertContains(review, MOTION)
        self.assertContains(review, CLAUSE)
        self.assertContains(review, "Keep the signed approval.")
        self.assertNotContains(review, "[#record]")
        data = signing.loads(token, salt=discussion_forms.FORM_SALT)
        second = signing.loads(confirmed(self.author.post(endpoint, fields)), salt=discussion_forms.FORM_SALT)
        self.assertEqual(data, second)
        self.assertEqual(data["confirmation"]["predecessor"], str(self.binding))
        self.assertFalse(discussion._rows("SELECT 1 FROM public.core_cqact WHERE thread_uuid=%s", [live["thread_uuid"]]))
        forged = dict(fields, vote_command=token, predecessor=str(uuid4()), proposal=str(uuid4()), version="1", text="other")
        accepted = self.author.post(endpoint, forged)
        self.assertEqual(accepted.status_code, 303, "The reviewed v1 predecessor must reach the law writer.")
        self.assertEqual(self.author.post(endpoint, forged).status_code, 303)
        receipt, = discussion._rows("""SELECT act_uuid,request->'attachment' AS instrument FROM public.core_cqact
                                      WHERE thread_uuid=%s AND operation='INITIATE'""", [live["thread_uuid"]])
        self.assertEqual(receipt["act_uuid"], UUID(data["request"]))
        self.assertEqual(receipt["instrument"], self.instrument(UUID(data["confirmation"]["proposal"])))
        cq.mark_prepared(**live, request_uuid=uuid4())
        ballot = cq_reads.vote_status(**live)["ballot"]
        self.answers(live, ballot, ["QUALIFIED"] * 3)
        self.assertEqual(self.author.post(endpoint, forged).status_code, 303)
        latest = policy_adoption.current(self.org, self.resource)
        self.assertEqual(latest["version_number"], 2)
        refused = self.author.post(stale_endpoint, dict(stale_fields, vote_command=stale_token))
        self.assertContains(refused, "The adoption could not be put to a vote.", status_code=409)
        self.assertContains(refused, "This request started no vote.", status_code=409)
        page = Markup(refused.content.decode())
        card, = page.select("div", role="alert")
        action, = page.select("a", within=card)
        self.assertEqual((action["text"], action["attrs"]["href"]), ("Review the current Policy",
                         reverse("organisation_law_version", args=(self.org, latest["binding"], 2))))
        # Bypass the page comparator: an unused proposal and the exact old predecessor reach 0076.
        old = signing.loads(stale_token, salt=discussion_forms.FORM_SALT)
        with self.assertRaises(discussion.DiscussionRefused) as denied:
            cq.put_forward(**stale, version_uuid=opening, request_uuid=UUID(old["request"]),
                           attachment=self.instrument(UUID(old["confirmation"]["proposal"])))
        self.assertEqual(denied.exception.code, "CQ_ATTACHMENT")
        self.assertFalse(discussion._rows("SELECT 1 FROM public.core_cqact WHERE thread_uuid=%s", [stale["thread_uuid"]]))

    def test_prepare_exact_adoption(self):
        """L09-prepare. The author and Chair can prepare this adoption; a different trustee reads only."""
        self.board_policy(clarity_stage=True)
        journey, opening, url = self.topic()
        self.assertNotContains(signed_in(self.trustees[2]).get(url), "Adopt a published Policy version")
        cq.put_forward(**journey, version_uuid=opening, request_uuid=uuid4(), attachment=self.instrument(uuid4()))
        first = self.author.get(url + "?prepare=1")
        self.assertContains(first, "Proceed to Clarity")
        self.assertContains(first, "Policy proposed for adoption · Version 2")
        self.assertContains(first, "The motion concerns version 2 of this Policy. Its text is fixed.")
        self.assertContains(first, CLAUSE)
        self.assertContains(first, MOTION)
        self.assertNotContains(signed_in(self.trustees[0]).get(url + "?prepare=1"), "Proceed to Clarity")
        chair = signed_in(self.trustees[2])
        self.assertEqual(preparation_receipts(journey["thread_uuid"]), [])
        endpoint, fields = command_form(chair.get(url + "?prepare=1"), "vote_command")
        self.assertEqual(chair.post(endpoint, fields).status_code, 303)
        self.assertEqual(cq.phase(**journey)["phase"], "CLARITY")
        self.assertEqual(preparation_receipts(journey["thread_uuid"])[0]["actor_id"], self.trustees[2].pk)

    def test_adopted_binding_receipt(self):
        """L09-bind. Only the matching implemented binding warrants the in-force notice on the member result."""
        self.board_policy()
        journey, opening, url = self.topic()
        proposal = uuid4()
        cq.put_forward(**journey, version_uuid=opening, request_uuid=uuid4(), attachment=self.instrument(proposal))
        reader = signed_in(laws.add_member(self.owner))
        self.assertNotContains(reader.get(url), CLAUSE)
        cq.mark_prepared(**journey, request_uuid=uuid4())
        ballot = cq_reads.vote_status(**journey)["ballot"]
        ballot_page = self.author.get(url)
        self.assertContains(ballot_page, "Policy proposed for adoption · Version 2")
        self.assertContains(ballot_page, CLAUSE)
        self.answers(journey, ballot, ["NOT_QUALIFIED", "QUALIFIED"])
        last = journey | {"credential": self.trustees[2].credential}
        args = (self.org, self.board.circle_uuid, journey["thread_uuid"])
        with transaction.atomic():
            cq.answer(**last, ballot_uuid=ballot, choice="NOT_QUALIFIED", request_uuid=uuid4())
            failed = cq_reads.vote_status(**journey, ballot_uuid=ballot)["result_uuid"]
            page = reader.get(reverse("discussion_result", args=(*args, failed)))
            self.assertContains(page, "Not carried")
            self.assertContains(page, "This vote did not put the Policy into force.")
            self.assertNotContains(page, "Version 2 is in force.")
            self.assertNotContains(page, CLAUSE)
            self.assertEqual(policy_adoption.current(self.org, self.resource), dict(binding=self.binding, version_number=1))
            transaction.set_rollback(True)
        cq.answer(**last, ballot_uuid=ballot, choice="QUALIFIED", request_uuid=uuid4())
        result = cq_reads.vote_status(**journey, ballot_uuid=ballot)["result_uuid"]
        result_url = reverse("discussion_result", args=(*args, result))
        page = reader.get(result_url)
        self.assertContains(page, "Version 2 is in force.", count=1)
        self.assertContains(page, MOTION)
        self.assertNotContains(page, CLAUSE)
        self.assertNotContains(page, "/laws/")
        record, = discussion._rows("""SELECT b.binding_uuid,b.predecessor_id,b.version_id,i.actor_id
            FROM public.core_cqact i JOIN public.core_livingorganismevent e ON e.event_uuid=i.act_uuid
            JOIN public.core_organismlawbinding b ON b.adoption_event_id=e.id
            WHERE i.operation='IMPLEMENT' AND i.request->>'result'=%s""", [str(result)])
        self.assertEqual((record["binding_uuid"], record["predecessor_id"], record["version_id"], record["actor_id"]),
                         (proposal, self.binding, self.v2.pk, self.trustees[2].pk))
        self.assertEqual(policy_adoption.current(self.org, self.resource), dict(binding=proposal, version_number=2))
        # Read the same carried result with individual pieces of evidence withheld.
        # Only the test's relation inputs change. The production predicates still decide.
        for relation, projection in (
            ("public.core_cqact", "(SELECT * FROM public.core_cqact WHERE operation <> 'IMPLEMENT')"),
            ("public.core_organismlawbinding",
             "unnest(ARRAY(SELECT old FROM public.core_organismlawbinding old WHERE old.predecessor_id IS NULL))"),
        ):
            with self.subTest(withheld=relation), patch.object(
                cq_reads, "_RESULT_DETAIL", cq_reads._RESULT_DETAIL.replace(relation + " ", projection + " ")
            ):
                incomplete = reader.get(result_url)
                self.assertContains(incomplete, MOTION)
                self.assertNotContains(incomplete, "Version 2 is in force.")
                self.assertNotContains(incomplete, "This vote did not put the Policy into force.")
                self.assertNotContains(incomplete, CLAUSE)


class AdoptionFormTests(SimpleTestCase):
    """Exercise signed forms, the exact review and all recovery routes without a database."""

    def test_signed_review_and_recovery(self):
        """Use the real HTTP boundary while the reader and writer return controlled facts."""
        with projected_forms() as (client, org, circle, _, thread), ExitStack() as stack:
            url = reverse("discussion_topic", args=(org, circle, thread))
            _, context = discussion_pages._read_page(None, org, circle, thread)
            context["topic"]["topic_type"] = "SUGGESTION"
            context["posts"][0]["hidden"] = False
            context["organisation"]["full_name"] = "Example Trust"
            opening = context["posts"][0]["version_uuid"]
            binding = uuid4()
            item = dict(binding=binding, reference="duties", title="Treasurer duties", previous_number=1, version_number=2)
            panel = dict(operation="INITIATE", opening_version=opening, motion=MOTION, publications=[], adoptions=[item])
            mock = stack.enter_context
            mock(patch.object(discussion_forms, "_read_page", return_value=("core/discussion_topic.html", context)))
            mock(patch.object(discussion_vote.transaction, "atomic", side_effect=lambda: nullcontext()))
            mock(patch.object(discussion, "_actor"))
            mock(patch.object(cq, "_lock"))
            mock(patch.object(discussion_forms, "_back_organisation", return_value=None))
            vote = mock(patch.object(cq, "board_topic_vote", return_value=panel))
            wording = mock(patch.object(policy_adoption, "wording", return_value=TEXT))
            latest = dict(binding=uuid4(), version_number=3)
            mock(patch.object(policy_adoption, "current", return_value=latest))
            mock(patch.object(policy_publication, "fingerprint", return_value="fixed"))
            receipt = mock(patch.object(policy_publication, "recorded_attachment", return_value=None))
            writer = mock(patch.object(cq, "put_forward"))
            chooser = client.get(url)
            endpoint, csrf, options = commands(chooser)
            plain, candidate = options
            self.assertEqual(plain["text"], "Proposal only")
            self.assertIn("Adopt a published Policy version", candidate["text"])
            markup = Markup(chooser.content.decode())
            select, = markup.select("select", name="vote_command")
            form = next(p for p in select["parents"] if p["tag"] == "form")
            button, = markup.select("button", within=form)
            self.assertIn("discussion-actions", button["parents"][-1]["attrs"]["class"].split())
            fields = dict(csrfmiddlewaretoken=csrf, vote_command=candidate["attrs"]["value"])
            review = client.post(endpoint, fields)
            self.assertContains(review, "Keep the signed approval.")
            self.assertContains(review, "Publication made this wording available.")
            self.assertContains(review, MOTION)
            self.assertNotContains(review, "[#approval]")
            token = confirmed(review)
            data = signing.loads(token, salt=discussion_forms.FORM_SALT)
            first = signing.loads(fields["vote_command"], salt=discussion_forms.FORM_SALT)
            self.assertNotIn("proposal", first["confirmation"])
            self.assertEqual(first["request"], data["request"])
            self.assertEqual(signing.loads(confirmed(client.post(endpoint, fields)), salt=discussion_forms.FORM_SALT), data)
            self.assertTrue(all(token not in a["attrs"].get("href", "") for a in Markup(review.content.decode()).select("a")))
            writer.assert_not_called()
            forged = dict(fields, vote_command=token, resource="unrelated", version=1, predecessor=str(uuid4()))
            self.assertEqual(client.post(endpoint + "?version=99", forged).status_code, 303)
            expected = LawProposal(organism=org, resource="duties", proposal=UUID(data["confirmation"]["proposal"]),
                                   predecessor=binding, version=2, law_class="POLICY", operation="ADOPT", intent="ADOPT")
            self.assertEqual(writer.call_args.kwargs["attachment"], dict(kind="LAW", spec=expected.content(), text=TEXT))
            self.assertEqual(writer.call_args.kwargs["request_uuid"], UUID(data["request"]))
            writer.reset_mock()
            vote.return_value = dict(panel, adoptions=[dict(item, binding=latest["binding"])])
            stale = client.post(endpoint, forged)
            self.assertContains(stale, "The adoption could not be put to a vote.", status_code=409)
            self.assertContains(stale, "The published wording or the law it would replace has changed.", status_code=409)
            self.assertContains(stale, "This request started no vote.", status_code=409)
            self.assertContains(stale, "If you need help, contact a Board Coordinator.", status_code=409)
            markup = Markup(stale.content.decode())
            card, = markup.select("div", role="alert")
            link, = markup.select("a", within=card)
            self.assertEqual((link["text"], link["attrs"]["href"]), ("Review the current Policy",
                             reverse("organisation_law_version", args=(org, latest["binding"], 3))))
            vote.return_value = {}
            self.assertContains(client.post(endpoint, forged), "Your vote action could not be recorded.", status_code=409)
            vote.return_value = panel
            bad = dict(forged, vote_command=token + "x")
            self.assertContains(client.post(endpoint, bad), "The form no longer matches", status_code=400)
            other_topic = reverse("discussion_put_forward", args=(org, circle, uuid4()))
            self.assertContains(client.post(other_topic, forged), "Discussion unavailable", status_code=404)
            wording.side_effect = DatabaseError()
            self.assertContains(client.post(endpoint, fields), "This request started no vote.", status_code=503)
            writer.assert_not_called()
            wording.side_effect = None
            writer.side_effect = DatabaseError()
            self.assertContains(client.post(endpoint, forged), "A vote action may already be recorded.", status_code=503)
            writer.side_effect = discussion.DiscussionRefused(code="CQ_ATTACHMENT")
            self.assertContains(client.post(endpoint, forged), "Review the current Policy", status_code=409)
            receipt.return_value = dict(kind="LAW", spec=expected.content(), text=TEXT)
            writer.side_effect = None
            vote.reset_mock()
            self.assertEqual(client.post(endpoint, forged).status_code, 303)
            vote.assert_not_called()
            self.assertEqual(writer.call_args.kwargs["attachment"], receipt.return_value)
