"""Box 12a. The exact law, payment citation, draft receipt and drafter's declaration."""

from uuid import uuid4

from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from core.models import LibraryResourceVersion
from src.intevia.services import cq, law_acts, library_laws, library_policy
from tests import cq_authority_support as boards, cq_settings_support as ballots
from tests import test_cq_clarity as clarity, test_law_acts as laws
from tests.office_record_support import a1_adopt_policy
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_pages import FIXTURE_HASHERS, activate_trustees


FIRST_TEXT = "[#approval] The Treasurer approves ordinary payments."
SECOND_TEXT = "When the Treasurer office is vacant, the Board approves ordinary payments."
PAYMENT = "Approve the £84 community room repair."


def hidden_fields(response):
    """Take the hidden values from the rendered Policy form, including its CSRF token."""
    page = Markup(response.content.decode())
    form, = page.select("form", id="policy-form")
    return {node["attrs"]["name"]: node["attrs"]["value"]
            for node in page.select("input", within=form, type="hidden")}


def board_result(case, *, publication=False, first_text=FIRST_TEXT):
    """Adopt v1 before Board activation, then settle one real, Clarity-off motion."""
    owner, scope, trustees, board = boards.ordinary(3, board=True)
    founder, officer = trustees[:2]
    ref = "repair-duties-" + uuid4().hex
    first, binding, adopted = a1_adopt_policy(owner, founder, ref, first_text)
    case.assertEqual(adopted.status, "ADOPT", "The film's predecessor must already govern.")
    activate_trustees(scope, board)
    boards.appoint_board_coordinator(owner, board, founder, officer)
    local = dict(scope, circle_uuid=board.circle_uuid, credential=officer.credential)
    attachment = None
    if publication:
        other = "another-policy-" + uuid4().hex
        work = library_policy.draft_policy(**scope, resource_reference=other, content="Another policy.",
            authors=[founder.identity_id], declare_own_authorship=True, request_uuid=uuid4())
        attachment = dict(kind="PUBLISH_POLICY", spec=dict(organism=str(owner.organism_id), resource=other,
                          version=work.version_number), text=work.content)
    words = "Publish the other Policy." if publication else PAYMENT
    journey, _, topic = clarity.CQClarityTests.topic(case, local, title=words, markdown=words)
    cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=topic["version"], attachment=attachment)
    ballot = ballots.open_motion(journey, topic["version"], trustees)
    result = ballots.settle(journey, ballot, trustees)
    case.assertEqual(result["count"]["kind"], "QUALIFIED", "This control needs a carried Board decision.")
    if publication:
        case.assertEqual(result.get("disposition"), "APPLIED", "The negative control must have a real attached publication.")
    link = reverse("discussion_result", args=(owner.organism_id, board.circle_uuid, topic["thread"], result["post_uuid"]))
    return owner, founder, first, binding, ref, link, result["post_uuid"]


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class PolicyRevisionTests(TransactionTestCase):
    """Each witness uses the shortest lawful route to the particular state it exercises."""

    def test_revision_replays_once_and_requires_reviewed_head(self):
        """L03. Valid signed save, one canonical payment citation, exact replay and stale refusal."""
        org, writer, first, binding, ref, link, result = board_result(self)
        client = signed_in(writer)
        scope = dict(credential=writer.credential, organism_uuid=org.organism_id)
        url = reverse("policy_revise", args=(org.organism_id, binding, 1))
        destination = reverse("policy_draft_receipt", args=(org.organism_id, ref, 2))
        old_forms = []
        for _ in range(2):
            page = client.get(url)
            self.assertContains(page, "Draft the next version")
            review = client.post(url, hidden_fields(page) | dict(action="review", payment=link, text_0=SECOND_TEXT))
            self.assertContains(review, PAYMENT)
            self.assertContains(review, "Board of Trustees")
            old_forms.append(hidden_fields(review) | dict(action="save", text_0=SECOND_TEXT))
        versions = LibraryResourceVersion.objects.filter(resource=first.resource)
        self.assertEqual(versions.count(), 1, "Reviewing a decision or opening another form must not make a draft.")
        missing = client.post(url, old_forms[0] | {"text_0": "   "})
        self.assertContains(missing, "Correct the form below and submit it again.", status_code=400)
        self.assertContains(missing, "Save draft", status_code=400)
        self.assertEqual(versions.count(), 1)
        for _ in range(2):
            saved = client.post(url, old_forms[0])
            self.assertEqual((saved.status_code, saved.get("Location")), (303, destination),
                             "The save and its identical replay must both reach the same draft receipt.")
        second = versions.get(version_number=2)
        self.assertEqual(second.predecessor_id, first.pk)
        self.assertEqual(second.content, f"[#approval] {SECOND_TEXT}\n[#payment-result] {result}")
        self.assertContains(client.get(destination), "Version 2 is a draft. Version 1 still governs.")
        stale = client.post(url, old_forms[1])
        self.assertContains(stale, "newer version than the one you reviewed", status_code=409)
        self.assertContains(stale, "Review the Policy", status_code=409)
        with self.assertRaises(library_policy.PolicyRefused) as denied:
            library_policy.draft_policy(**scope, resource_reference=ref, content=SECOND_TEXT, authors=[writer.identity_id],
                predecessor_version_id=first.pk, request_uuid=uuid4())
        self.assertEqual(denied.exception.code, "LIB_STALE_HEAD", "The storage-facing writer also rejects the old predecessor.")
        self.assertEqual(versions.count(), 2, "A stale or replayed save must not create a third version.")
        current, status = library_laws.our_law_version(**scope, binding=binding, version_number=1)
        self.assertEqual((status, current["content"]), ("In force now", FIRST_TEXT))

    def test_only_effective_reviser_gets_the_form(self):
        """L03-owner. A member can read the same law without gaining the reviser's form or POST."""
        org, writer = laws.ordinary_birth()
        member = laws.add_member(org)
        ref = "local-policy-" + uuid4().hex
        first, binding, adopted = a1_adopt_policy(org, writer, ref, FIRST_TEXT)
        self.assertEqual(adopted.status, "ADOPT")
        url = reverse("policy_revise", args=(org.organism_id, binding, 1))
        detail = reverse("organisation_law_version", args=(org.organism_id, binding, 1))
        permitted, ordinary = signed_in(writer), signed_in(member)
        self.assertContains(permitted.get(detail), f'href="{url}"')
        signed = hidden_fields(permitted.get(url))
        denied = ordinary.get(url)
        self.assertEqual(denied.status_code, 404, "An ordinary member must not open the revision form directly.")
        self.assertNotContains(ordinary.get(detail), "Revise this Policy")
        for response in (ordinary.post(url, signed), ordinary.get(reverse("policy_revise", args=(org.organism_id, uuid4(), 1))),
                         permitted.post(reverse("policy_revise", args=(org.organism_id, binding, 2)), signed)):
            self.assertContains(response, "This page is unavailable to you.", status_code=404)
            self.assertNotContains(response, FIRST_TEXT, status_code=404)
        self.assertEqual(LibraryResourceVersion.objects.filter(resource=first.resource).count(), 1)

    def test_attached_result_cannot_supply_a_payment_citation(self):
        """L03-pay. Even a qualified, independently visible publication cannot be cited as the payment."""
        org, writer, first, binding, _, link, _ = board_result(self, publication=True)
        client = signed_in(writer)
        self.assertContains(client.get(link), "Publish the other Policy.")
        url = reverse("policy_revise", args=(org.organism_id, binding, 1))
        fields = hidden_fields(client.get(url)) | dict(action="review", text_0=SECOND_TEXT)
        page = client.post(url, fields | {"payment": link})
        self.assertEqual(page.status_code, 404, "An attached Board result must not become a reviewed payment citation.")
        self.assertContains(page, "This page is unavailable to you.", status_code=404)
        foreign = link.replace(str(org.organism_id), str(uuid4()))
        self.assertContains(client.post(url, fields | {"payment": foreign}), "This page is unavailable to you.", status_code=404)
        self.assertEqual(LibraryResourceVersion.objects.filter(resource=first.resource).count(), 1)

    def test_drafter_explicit_declaration_and_private_receipt(self):
        """L04-own. Credit, access, explicit affirmation, one claim on replay and its recorded time."""
        org, drafter = laws.ordinary_birth()
        prior = laws.add_member(org)
        ref = "declaration-" + uuid4().hex
        scope = dict(credential=drafter.credential, organism_uuid=org.organism_id)
        first = library_policy.draft_policy(**scope, resource_reference=ref, content=FIRST_TEXT,
            authors=[prior.identity_id], request_uuid=uuid4())
        library_policy.declare_authorship(credential=prior.credential, version_id=first.pk,
            content_sha256=library_policy._meta(first.pk)["content_sha256"], authors=[prior.identity_id], affirm=True)
        library_policy.publish_policy(**scope, version_id=first.pk, request_uuid=uuid4())
        proposal = law_acts.LawProposal(organism=org.organism_id, resource=ref, proposal=uuid4(),
            operation="ADOPT", intent="ADOPT", law_class="POLICY", version=1)
        self.assertEqual(law_acts.OrganismLawService().adopt_law(laws.command(drafter, proposal)).status, "ADOPT")
        second = library_policy.draft_policy(**scope, resource_reference=ref, content="[#approval] " + SECOND_TEXT,
            authors=[prior.identity_id, drafter.identity_id], predecessor_version_id=first.pk, request_uuid=uuid4())
        url = reverse("policy_draft_receipt", args=(org.organism_id, ref, 2))
        self.assertContains(signed_in(prior).get(url), "This page is unavailable to you.", status_code=404)
        client = signed_in(drafter)
        page = client.get(url)
        self.assertContains(page, "Carried from version 1")
        self.assertContains(page, "Clause “approval”")
        self.assertNotContains(page, "[#approval]")
        fields = hidden_fields(page)
        claim_sql = ("SELECT recorded_at FROM public.core_libauthorclaim WHERE version_id=%s AND author_id=%s "
                     "AND inherited_from IS NULL")
        params = [second.pk, drafter.pk]
        absent = client.post(url, fields)
        self.assertEqual(library_policy._rows(claim_sql, params), [], "Missing affirmation must record zero own claims.")
        self.assertContains(absent, "Correct the form below and submit it again.", status_code=400)
        for _ in range(2):
            posted = client.post(url, fields | {"affirm": "yes"})
            self.assertEqual((posted.status_code, posted.get("Location")), (303, url))
        claim, = library_policy._rows(claim_sql, params)
        page = client.get(url)
        self.assertContains(page, "Declaration recorded")
        self.assertContains(page, library_policy.AUTHOR_DECLARATION)
        self.assertContains(page, claim["recorded_at"].strftime("%H:%M") + " UTC")
        self.assertNotContains(page, 'name="affirm"')
        carried, = library_policy._rows("SELECT inherited_from FROM public.core_libauthorclaim WHERE version_id=%s "
                                        "AND author_id=%s", [second.pk, prior.pk])
        original, = library_policy._rows("SELECT claim_uuid FROM public.core_libauthorclaim WHERE version_id=%s", [first.pk])
        self.assertEqual(carried["inherited_from"], original["claim_uuid"])

    def test_payment_citation_in_words_on_receipt_and_governing_law(self):
        """L03-word. Both real pages release this payment only under the viewer’s current result grant."""
        from django.utils.dateparse import parse_datetime
        from src.intevia.services import cq_reads, discussion_posts

        org, scope, trustees, board = boards.ordinary(3, board=True)
        drafter, coordinator = trustees[:2]
        ref = "cited-repair-" + uuid4().hex
        first, binding, adopted = a1_adopt_policy(org, drafter, ref, FIRST_TEXT)
        self.assertEqual(adopted.status, "ADOPT")
        activate_trustees(scope, board)
        boards.appoint_board_coordinator(org, board, drafter, coordinator)
        local = scope | dict(credential=coordinator.credential, circle_uuid=board.circle_uuid)
        self.topic_category_ids = {}

        def vote(title, attachment=None):
            """Reach a recorded result through the landed Clarity-off Board route."""
            journey, category, topic = clarity.CQClarityTests.topic(self, local, title=title, markdown=title)
            cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=topic["version"], attachment=attachment)
            ballot = ballots.open_motion(journey, topic["version"], trustees)
            result = ballots.settle(journey, ballot, trustees)
            self.assertEqual(result["count"]["kind"], "QUALIFIED")
            if attachment:
                self.assertEqual(result.get("disposition"), "APPLIED")
            return journey, category, topic, result

        journey, category, topic, settled = vote(PAYMENT)
        result_id = settled["post_uuid"]
        result = cq_reads.result_post(**journey, result_uuid=result_id, governance_only=True, detail=True)
        link = reverse("discussion_result", args=(org.organism_id, board.circle_uuid, topic["thread"], result_id))
        text = FIRST_TEXT + "\n[#payment-result] " + str(result_id)
        second = library_policy.draft_policy(**scope, resource_reference=ref, content=text,
            authors=[drafter.identity_id], declare_own_authorship=True, predecessor_version_id=first.pk, request_uuid=uuid4())
        client = signed_in(drafter)

        def check_page(url):
            """Hide and restore the real result while the same reader keeps the enclosing page."""
            def citation(response):
                """Read the labelled paragraph within the law card; exclude attributes from visible text."""
                self.assertEqual(response.status_code, 200)
                page = Markup(response.content.decode())
                card, = page.select("article", classes=("office-provision",))
                paragraphs = page.select("p", within=card)
                label, = [i for i, node in enumerate(paragraphs) if node["text"] == "Payment decision cited"]
                self.assertNotIn(str(result_id), page.select("main")[0]["text"])
                return page, paragraphs[label + 1]

            visible, row = citation(client.get(url))
            self.assertIn(PAYMENT, row["text"], "Show the payment’s decided wording in the cited clause.")
            self.assertIn(result["deciding_body"]["full_name"], row["text"])
            stamp, = visible.select("time", within=row)
            self.assertEqual(parse_datetime(stamp["attrs"]["datetime"]), result["recorded_at"])
            anchor, = visible.select("a", within=row)
            self.assertEqual((anchor["text"], anchor["attrs"]["href"]), ("View vote result", link))
            hide = dict(**journey, category_uuid=category, post_uuid=topic["post"], as_coordinator=True)
            discussion_posts.set_post_hidden(**hide, hidden=True, request_uuid=uuid4())
            self.assertEqual(client.get(link).status_code, 404, "The independent result must really be withheld.")
            refused = client.get(url)
            private, row = citation(refused)
            self.assertEqual(row["text"], "This payment decision is unavailable to you.")
            self.assertFalse(private.select("time", within=row))
            self.assertFalse(private.select("a", within=row))
            self.assertNotIn(str(result_id), refused.content.decode(), "Withholding covers the address as well as the text.")
            discussion_posts.set_post_hidden(**hide, hidden=False, request_uuid=uuid4())

        check_page(reverse("policy_draft_receipt", args=(org.organism_id, ref, second.version_number)))
        vote("Publish the revised Policy.", dict(kind="PUBLISH_POLICY", text=text, spec=dict(
            organism=str(org.organism_id), resource=ref, version=second.version_number)))
        proposal = law_acts.LawProposal(organism=org.organism_id, resource=ref, proposal=uuid4(),
            predecessor=binding, operation="ADOPT", intent="ADOPT", law_class="POLICY", version=second.version_number)
        vote("Adopt the revised Policy.", dict(kind="LAW", spec=proposal.content(), text=text))
        check_page(reverse("organisation_law_version", args=(org.organism_id, proposal.proposal, second.version_number)))
        second.refresh_from_db()
        self.assertEqual(second.content, text, "Both pages must preserve the exact stored citation.")

    def test_stored_heading_survives_revision_and_stays_out_of_clause_boxes(self):
        """R1-heading. The reader edits the clause and votes on the exact stored, titled successor."""
        from django.urls import resolve
        from tests.test_policy_vote_pages import commands, confirmed

        heading = "# Treasurer duties \t\r\n"
        owner, writer, first, binding, ref, payment, result_id = board_result(
            self, first_text=heading + FIRST_TEXT)
        org = owner.organism_id
        client = signed_in(writer)
        revise = reverse("policy_revise", args=(org, binding, 1))
        form = client.get(revise)
        self.assertContains(form, "Draft the next version")
        fields = Markup(form.content.decode()).select("textarea")
        self.assertEqual([f["text"] for f in fields], [FIRST_TEXT.split("] ", 1)[1]],
                         "The stored title must never be an editable Wording field.")
        entry = {fields[0]["attrs"]["name"]: SECOND_TEXT}
        review = client.post(revise, hidden_fields(form) | entry | dict(action="review", payment=payment))
        saved = client.post(revise, hidden_fields(review) | entry | dict(action="save"))
        receipt_url = reverse("policy_draft_receipt", args=(org, ref, 2))
        self.assertEqual((saved.status_code, saved.get("Location")), (303, receipt_url))
        second = LibraryResourceVersion.objects.get(resource=first.resource, version_number=2)
        expected = heading + "[#approval] " + SECOND_TEXT + "\n[#payment-result] " + str(result_id)
        self.assertEqual(second.content.encode("utf-8"), expected.encode("utf-8"))

        def clause_lines(response):
            """Read visible clause lines and require the revised wording on each checked page."""
            self.assertEqual(response.status_code, 200)
            boxes = Markup(response.content.decode()).select("div", classes=("office-clause",))
            self.assertIn(SECOND_TEXT, [box["text"] for box in boxes])
            return [line for box in boxes for line in box["text"].splitlines()]

        receipt = client.get(receipt_url)
        rendered = {"draft receipt": clause_lines(receipt)}
        self.assertFalse(any(line.lstrip().startswith("#") for line in rendered["draft receipt"]),
                         "The stored title marker must stay outside the receipt’s clause boxes.")
        self.assertEqual(client.post(receipt_url, hidden_fields(receipt) | {"affirm": "yes"}).status_code, 303)
        circle = resolve(payment).kwargs["circle"]
        scope = dict(credential=writer.credential, organism_uuid=org, circle_uuid=circle)

        def open_review(verb):
            """Use this Board member's actual signed choice to review the exact successor."""
            journey, _, topic = clarity.CQClarityTests.topic(self, scope, title=verb, markdown=verb)
            url = reverse("discussion_topic", args=(org, circle, topic["thread"]))
            endpoint, csrf, choices = commands(client.get(url))
            choice, = [c for c in choices if verb in c["text"] and ref in c["text"]]
            fields = dict(csrfmiddlewaretoken=csrf, vote_command=choice["attrs"]["value"])
            review = client.post(endpoint, fields)
            rendered[verb + " review"] = clause_lines(review)
            return journey, topic, url, endpoint, fields | {"vote_command": confirmed(review)}

        journey, topic, url, endpoint, confirm = open_review("Publish a Policy version")
        self.assertEqual(client.post(endpoint, confirm).status_code, 303)
        rendered["publication vote"] = clause_lines(client.get(url))
        voters = ballots.voters(scope)
        ballot = ballots.open_motion(journey, topic["version"], voters)
        self.assertEqual(ballots.settle(journey, ballot, voters)["disposition"], "APPLIED")
        _, _, url, endpoint, confirm = open_review("Adopt a published Policy version")
        self.assertEqual(client.post(endpoint, confirm).status_code, 303)
        rendered["adoption vote"] = clause_lines(client.get(url))
        for surface, lines in rendered.items():
            with self.subTest(surface=surface):
                self.assertFalse(any(line.lstrip().startswith("#") for line in lines))
        second.refresh_from_db()
        self.assertEqual(second.content.encode("utf-8"), expected.encode("utf-8"))
