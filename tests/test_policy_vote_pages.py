"""13a. Publish a particular ready successor from the Board author's own Suggestion."""

from contextlib import ExitStack, nullcontext
from unittest.mock import patch
from uuid import UUID, uuid4

from django.core import signing
from django.db import DatabaseError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core import discussion_forms, discussion_pages, discussion_vote
from src.intevia.services import cq, discussion, library_policy, policy_publication
from tests import cq_authority_support as boards
from tests import test_cq_clarity as clarity
from tests.office_record_support import a1_adopt_policy
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_pages import FIXTURE_HASHERS, activate_trustees
from tests.test_uc23_shell import projected_forms


def commands(response):
    """Read the default choice, signed alternatives and CSRF field from the actual form."""
    page = Markup(response.content.decode())
    select, = page.select("select", name="vote_command")
    form = next(p for p in select["parents"] if p["tag"] == "form")
    csrf, = page.select("input", within=form, name="csrfmiddlewaretoken")
    choices = page.select("option", within=select)
    return form["attrs"]["action"], csrf["attrs"]["value"], choices


def confirmed(response):
    """Read the signed confirmation exactly as a browser would submit it."""
    page = Markup(response.content.decode())
    token, = page.select("input", name="vote_command")
    return token["attrs"]["value"]


class PublicationChoiceTests(SimpleTestCase):
    """Exercise real CSRF, signing, routes and templates against released projections."""

    def test_choice_preserves_attachment(self):
        """L08-choice. Extra browser fields cannot convert a plain vote or retarget a reviewed one."""
        with projected_forms() as (client, org, circle, _, thread), ExitStack() as stack:
            address = reverse("discussion_topic", args=(org, circle, thread))
            _, base_context = discussion_pages._read_page(None, org, circle, thread)
            base_context["topic"].update(topic_type="SUGGESTION")
            base_context["posts"][0]["hidden"] = False
            base_context["organisation"]["full_name"] = "Example Trust"
            opening = base_context["posts"][0]["version_uuid"]
            item = dict(reference="repair-duties", title="Treasurer office", version_number=2)
            ready = dict(operation="INITIATE", opening_version=opening, motion="Publish the revised policy.",
                         publications=[item])
            current = dict(current=True, declared=True, wording="[#approval] The Board approves ordinary payments.")
            patcher = stack.enter_context
            patcher(patch.object(discussion_forms, "_read_page", return_value=("core/discussion_topic.html", base_context)))
            patcher(patch.object(discussion_vote.transaction, "atomic", side_effect=lambda: nullcontext()))
            patcher(patch.object(discussion, "_actor"))
            patcher(patch.object(cq, "_lock"))
            patcher(patch.object(discussion_forms, "_back_organisation", return_value=None))
            panel = patcher(patch.object(cq, "board_topic_vote", return_value=ready))
            read = patcher(patch.object(policy_publication, "exact", return_value=current))
            digest = patcher(patch.object(policy_publication, "fingerprint", return_value="reviewed-fingerprint"))
            patcher(patch.object(policy_publication, "recorded_attachment", return_value=None))
            write = patcher(patch.object(cq, "put_forward"))
            page = client.get(address)
            endpoint, csrf, options = commands(page)
            plain, publication = options
            self.assertEqual(plain["text"], "Proposal only")
            self.assertIn("selected", plain["attrs"])
            self.assertIn("Publish a Policy version", publication["text"])
            self.assertNotContains(page, current["wording"])

            def submit(token, **extra):
                """Send the signed value alongside arbitrary browser-supplied fields."""
                return client.post(endpoint, dict(vote_command=token, csrfmiddlewaretoken=csrf, **extra))

            plain_result = submit(plain["attrs"]["value"], version="999", resource="other", intent="publish")
            self.assertEqual(plain_result.status_code, 303)
            self.assertNotIn("attachment", write.call_args.kwargs, "A forged version on a plain form attaches nothing.")
            read.assert_not_called()
            write.reset_mock()
            choice = publication["attrs"]["value"]
            review = submit(choice, version="999", resource="other")
            self.assertContains(review, "The Board approves ordinary payments.")
            self.assertContains(review, ready["motion"])
            self.assertContains(review, "Version 2 · Draft")
            self.assertContains(review, "Put publication to a Board vote")
            self.assertNotContains(review, "[#approval]")
            cancel, = [a for a in Markup(review.content.decode()).select("a") if a["text"] == "Cancel"]
            self.assertEqual(cancel["attrs"]["href"], address + "#vote")
            client.get(cancel["attrs"]["href"])
            write.assert_not_called()
            final = confirmed(review)
            issued = signing.loads(choice, salt=discussion_forms.FORM_SALT)
            reviewed = signing.loads(final, salt=discussion_forms.FORM_SALT)
            self.assertEqual(reviewed["request"], issued["request"])
            self.assertNotEqual(reviewed["request"], signing.loads(plain["attrs"]["value"],
                                                                  salt=discussion_forms.FORM_SALT)["request"])
            self.assertEqual(reviewed["confirmation"], dict(intent="publish", step="confirm",
                resource=item["reference"], number=2, fingerprint="reviewed-fingerprint"))
            self.assertEqual(submit(final, version="1", text="replacement").status_code, 303)
            self.assertEqual(write.call_args.kwargs.get("attachment"), {
                "kind": "PUBLISH_POLICY", "spec": {"organism": str(org), "resource": item["reference"], "version": 2},
                "text": current["wording"]}, "The confirmed writer receives exactly the reviewed attachment.")
            write.assert_called_once()
            self.assertEqual(write.call_args.kwargs["version_uuid"], opening)
            self.assertEqual(write.call_args.kwargs["request_uuid"], UUID(reviewed["request"]))
            self.assertEqual((write.call_args.kwargs["organism_uuid"], write.call_args.kwargs["circle_uuid"],
                              write.call_args.kwargs["thread_uuid"]), (org, circle, thread))
            for response, control in [(page, "select"), (review, "input")]:
                markup = Markup(response.content.decode())
                value, = markup.select(control, name="vote_command")
                parent = next(n for n in value["parents"] if n["tag"] == "form")
                self.assertEqual((parent["attrs"]["method"], parent["attrs"]["action"]), ("post", endpoint))
                self.assertFalse(any(token in a["attrs"].get("href", "") for a in markup.select("a")
                                     for token in (choice, final)))
            write.reset_mock()

            digest.return_value = "changed-fingerprint"
            stale = submit(final)
            self.assertContains(stale, "The version has changed since you reviewed it.", status_code=409)
            self.assertContains(stale, "Review the version", status_code=409)
            self.assertContains(submit(confirmed(stale)), "Review publication")
            panel.return_value = dict(ready, publications=[])
            read.return_value = dict(current=False, declared=True, wording=None)
            retired = submit(final)
            self.assertContains(submit(confirmed(retired)), "no longer available as the current draft", status_code=409)
            panel.return_value = ready
            read.return_value = dict(current=True, declared=False, wording=None)
            self.assertContains(submit(final), "still needs to declare their authorship", status_code=409)
            read.side_effect = discussion.DiscussionUnavailable()
            unknown = submit(final)
            substituted = client.post(reverse("discussion_put_forward", args=(org, circle, uuid4())),
                                      dict(vote_command=final, csrfmiddlewaretoken=csrf))
            self.assertContains(unknown, "Discussion unavailable", status_code=404)
            self.assertEqual((substituted.status_code, substituted.content), (404, unknown.content))
            read.side_effect = None
            panel.return_value = {}
            self.assertContains(submit(final), "Your vote action could not be recorded.", status_code=409)
            self.assertNotContains(client.get(address), "Publish a Policy version")
            panel.return_value = ready
            read.side_effect = DatabaseError()
            self.assertContains(submit(choice), "This request started no vote.", status_code=503)
            read.side_effect = None
            write.assert_not_called()
            read.return_value = current
            digest.return_value = "reviewed-fingerprint"
            write.side_effect = DatabaseError()
            unconfirmed = submit(final)
            self.assertContains(unconfirmed, "A vote action may already be recorded.", status_code=503)
            self.assertContains(unconfirmed, "If you need help, contact a Board Coordinator.", status_code=503)
            page = Markup(unconfirmed.content.decode())
            card, = page.select("div", role="alert")
            check, = page.select("a", classes=("secondary",), within=card)
            self.assertEqual((check["attrs"]["href"], check["text"]),
                             (address + "#vote", "Check the Topic before trying the same submission again."))


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class PolicyPublicationTests(TransactionTestCase):
    """Exercise the live authorship, Board authority, attachment and replay guards."""

    def test_publication_put_forward(self):
        """L08. Only the exact ready successor reaches INITIATE. Publication itself is a later act."""
        owner, scope, trustees, board = boards.ordinary(3, board=True)
        writer, proposer = trustees[:2]
        org = owner.organism_id
        resource = "treasurer-duties-" + uuid4().hex
        v1, _, adoption = a1_adopt_policy(owner, writer, resource, "[#approval] The Treasurer approves.")
        self.assertEqual(adoption.status, "ADOPT")
        wording = "# Treasurer duties\n[#approval] The Board approves while the office is vacant."
        v2 = library_policy.draft_policy(**scope, resource_reference=resource, content=wording,
            authors=[writer.identity_id, proposer.identity_id], request_uuid=uuid4(), predecessor_version_id=v1.pk)
        unrelated = "library-only-" + uuid4().hex
        library_policy.draft_policy(**scope, resource_reference=unrelated, content="Other unpublished work.",
            authors=[writer.identity_id], request_uuid=uuid4(), declare_own_authorship=True)
        activate_trustees(scope, board)
        boards.appoint_board_coordinator(owner, board, writer, proposer)
        local = dict(scope, credential=proposer.credential, circle_uuid=board.circle_uuid)
        motion = "Put the revised Treasurer duties forward for publication."
        _, _, topic = clarity.CQClarityTests.topic(self, local, title="Publication", markdown=motion)
        address = reverse("discussion_topic", args=(org, board.circle_uuid, topic["thread"]))
        client = signed_in(proposer)
        self.assertNotContains(client.get(address), "Publish a Policy version")
        authors = [writer.identity_id, proposer.identity_id]
        library_policy.declare_authorship(credential=proposer.credential, version_id=v2.pk,
            content_sha256=library_policy._meta(v2.pk)["content_sha256"], authors=authors, affirm=True)
        self.assertNotContains(signed_in(trustees[2]).get(address), "Publish a Policy version")
        endpoint, csrf, options = commands(client.get(address))
        self.assertFalse(any(unrelated in o["text"] for o in options), "A ready Library draft is not a law successor.")
        choice, = [o for o in options if resource in o["text"]]
        self.assertIn("Treasurer duties", choice["text"])
        fields = {"csrfmiddlewaretoken": csrf, "vote_command": choice["attrs"]["value"]}
        review = client.post(endpoint, fields)
        self.assertContains(review, "The Board approves while the office is vacant.")
        self.assertContains(review, motion)
        select_acts = """SELECT act_uuid, operation, request->'attachment' AS attachment,
                                facts->>'attachment_fingerprint' AS fingerprint,
                                facts->>'attachment_fingerprint'=public.disc_fingerprint(request->'attachment') AS exact
                           FROM public.core_cqact WHERE thread_uuid=%s ORDER BY ordinal"""
        self.assertEqual(discussion._rows(select_acts, [topic["thread"]]), [])
        fields.update(vote_command=confirmed(review), resource=unrelated, version="1")
        signed = signing.loads(fields["vote_command"], salt=discussion_forms.FORM_SALT)
        for _ in range(2):
            result = client.post(endpoint, fields)
            self.assertEqual((result.status_code, result.get("Location")), (303, address + "#vote"),
                             "The exact reviewed publication must reach the Topic, including on replay.")
        self.assertEqual(discussion._rows(select_acts, [topic["thread"]]), [
            dict(act_uuid=UUID(signed["request"]), operation="INITIATE", exact=True,
                 fingerprint=signed["confirmation"]["fingerprint"], attachment=dict(kind="PUBLISH_POLICY",
                 spec=dict(organism=str(org), resource=resource, version=2), text=wording))])
        self.assertEqual(library_policy._rows("SELECT 1 FROM public.core_libpublicationfact WHERE version_id=%s",
                                              [v2.pk]), [], "Putting a draft forward must never publish it.")
