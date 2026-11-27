"""13b. A Board prepares the fixed Policy instrument; a publication result releases only the outcome."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from django.db import transaction
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core import discussion_vote
from src.intevia.services import cq, cq_reads, discussion, discussion_posts, library_policy, policy_publication
from tests import cq_authority_support as boards
from tests import test_cq_clarity as clarity
from tests import test_law_acts as laws
from tests.office_record_support import a1_adopt_policy
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_pages import FIXTURE_HASHERS, activate_trustees, command_form, path, preparation_receipts
from tests.test_uc23_shell import projected_forms


MOTION = "Publish the revised duties for the Board to consider."
CLAUSE = "Two trustees check an ordinary payment during a vacancy."
TEXT = "[#approval] " + CLAUSE


def publication_motion(case, *, with_clarity):
    """Create the ready v2 before Board activation, then attach it to a genuine Suggestion."""
    owner, scope, trustees, board = boards.ordinary(3, board=True)
    founder, proposer, chair = trustees
    resource = "treasurer-duties-" + uuid4().hex
    v1, _, _ = a1_adopt_policy(owner, founder, resource, "[#approval] The Treasurer checks.")
    v2 = library_policy.draft_policy(**scope, resource_reference=resource, content=TEXT,
                                     authors=[founder.identity_id], declare_own_authorship=True,
                                     predecessor_version_id=v1.pk, request_uuid=uuid4())
    if with_clarity:
        boards.appoint_board_chairs(owner, founder, chair)
    activate_trustees(scope, board, clarity=with_clarity)
    boards.appoint_board_coordinator(owner, board, founder, proposer)
    board_scope = scope | {"credential": proposer.credential, "circle_uuid": board.circle_uuid}
    journey, _, topic = clarity.CQClarityTests.topic(case, board_scope, markdown=MOTION)
    cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=topic["version"],
                   attachment=policy_publication.attachment(owner.organism_id, resource, 2, TEXT))
    return owner, board, trustees, journey, v2


class PublicationLayoutTests(SimpleTestCase):
    """Read the actual template at the chooser and at each stage of the private vote."""

    def test_dy_choice_keeps_usual_button_layout(self):
        """DY1. Both forms give the submit button its landed flex container."""
        for choices in ([], [dict(title="Office", reference="duties", version_number=2, token="fixture")]):
            with self.subTest(chooser=bool(choices)):
                html = render_to_string("core/components/board_vote.html", dict(
                    vote=dict(publications=choices, command="fixture", label="Put to a Board vote",
                              route="discussion_put_forward"),
                    board_org=uuid4(), board_circle=uuid4(), topic={"thread_uuid": uuid4()}))
                markup = Markup(html)
                button, = markup.select("button")
                self.assertIn("discussion-actions", button["parents"][-1]["attrs"]["class"].split())
                self.assertEqual(len(markup.select("select")), int(bool(choices)))

    def test_word_motion_and_instrument_have_distinct_versions(self):
        """L08-word. Render the supplied motion and Policy versions separately. Service witness: test_prepare_attached_publication, line 99."""
        request = RequestFactory().get("/")
        request.user = object()
        now = datetime(2026, 11, 27, tzinfo=timezone.utc)
        for phase, heading in (("PREPARATION", "Wording to be decided"), ("CLARITY", "Wording to be decided"),
                               ("VOTING", "Wording being voted on")):
            with self.subTest(phase=phase):
                row = dict(phase=phase, markdown=MOTION, read_at=now, deadline=now + timedelta(days=1),
                           version_number=3, policy_version="2", policy_reference="treasurer-duties",
                           policy_kind="PUBLISH_POLICY", instrument_text=TEXT)
                with patch.object(cq, "board_topic_vote", return_value=row):
                    vote = discussion_vote.panel(request, uuid4(), uuid4(), uuid4())
                markup = Markup(render_to_string("core/components/board_vote.html", {"vote": vote}))
                headings = [h["text"].strip() for h in markup.select("h3")]
                self.assertIn("Policy proposed for publication · Version 2", headings)
                self.assertIn(f"Motion · {heading} · Version 3", headings)
                self.assertTrue(any(CLAUSE in node["text"] for node in markup.select("div", classes=("office-clause",))))
                self.assertTrue(any(MOTION in node["text"] for node in markup.select("div", classes=("motion-wording",))))


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class PublicationJourneyTests(TransactionTestCase):
    """Exercise real authority, signed POST, ballot, settlement and the member's independent result."""

    def test_prepare_attached_publication(self):
        """L08-prepare. Only the author or Chair gets the existing PREPARED confirmation."""
        owner, board, (other, author, chair), journey, _ = publication_motion(self, with_clarity=True)
        url = path(owner, board, journey["thread_uuid"])
        author_client, chair_client = signed_in(author), signed_in(chair)
        first = author_client.get(url)
        self.assertContains(first, "Mark as prepared", count=1)
        self.assertContains(first, "The motion wording can still change until voting opens. The Policy text is fixed.")
        self.assertContains(first, "Policy proposed for publication · Version 2")
        self.assertContains(first, CLAUSE)
        self.assertNotContains(first, "[#approval]")
        self.assertContains(chair_client.get(url), "Mark as prepared", count=1)
        refused = signed_in(other).get(url + "?prepare=1")
        self.assertContains(refused, CLAUSE)
        self.assertNotContains(refused, "Proceed to Clarity")
        self.assertNotContains(refused, "Mark as prepared")
        preview = author_client.get(url + "?prepare=1")
        self.assertContains(preview, "Proceed to Clarity")
        self.assertContains(preview, "The motion concerns version 2 of this Policy. Its text is fixed.")
        chair_preview = chair_client.get(url + "?prepare=1")
        self.assertEqual(preparation_receipts(journey["thread_uuid"]), [])
        endpoint, fields = command_form(chair_preview, "vote_command")
        self.assertEqual(chair_client.post(endpoint, fields).status_code, 303)
        self.assertEqual(cq.phase(**journey)["phase"], "CLARITY")
        receipts = preparation_receipts(journey["thread_uuid"])
        self.assertEqual([(r["actor_id"], r["authority_source"]["route"]) for r in receipts], [(chair.pk, "BOARD_CHAIR")])
        in_clarity = author_client.get(url)
        self.assertContains(in_clarity, CLAUSE)
        self.assertContains(in_clarity, MOTION)
        self.assertContains(in_clarity, "The Policy text is fixed.")

    def test_effect_matches_published_successor(self):
        """L08-effect. A different trustee settles and an unseated member reads the exact publication outcome."""
        owner, board, trustees, journey, version = publication_motion(self, with_clarity=False)
        founder, proposer, last = trustees
        outsider = laws.add_member(owner)
        reader = signed_in(outsider)
        url = path(owner, board, journey["thread_uuid"])
        topic = reader.get(url)
        self.assertNotContains(topic, CLAUSE)
        self.assertNotContains(topic, "Policy proposed for publication")
        author = signed_in(proposer)
        endpoint, fields = command_form(author.get(url + "?prepare=1"), "vote_command")
        self.assertEqual(author.post(endpoint, fields).status_code, 303)
        ballot = cq_reads.vote_status(**journey)["ballot"]
        ballot_page = author.get(url)
        self.assertContains(ballot_page, "Motion · Wording being voted on")
        self.assertContains(ballot_page, "Policy proposed for publication · Version 2")
        self.assertContains(ballot_page, CLAUSE)
        for voter, choice in ((proposer, "QUALIFIED"), (founder, "NOT_QUALIFIED")):
            cq.answer(**(journey | {"credential": voter.credential}), request_uuid=uuid4(),
                      ballot_uuid=ballot, choice=choice)
        final_scope = journey | {"credential": last.credential}
        # One real ballot. Roll back only the alternative last answer after reading its result.
        with transaction.atomic():
            cq.answer(**final_scope, request_uuid=uuid4(), ballot_uuid=ballot, choice="NOT_QUALIFIED")
            failed = cq_reads.vote_status(**journey, ballot_uuid=ballot)["result_uuid"]
            failed_url = reverse("discussion_result", args=(owner.organism_id, board.circle_uuid, journey["thread_uuid"], failed))
            withheld = reader.get(failed_url)
            self.assertContains(withheld, "The Policy was not published.")
            self.assertNotContains(withheld, CLAUSE)
            self.assertNotContains(withheld, "/laws/")
            self.assertFalse(discussion._rows("SELECT 1 FROM public.core_libpublicationfact WHERE version_id=%s",
                                              [version.pk]))
            transaction.set_rollback(True)
        cq.answer(**final_scope, request_uuid=uuid4(), ballot_uuid=ballot, choice="QUALIFIED")
        result = cq_reads.vote_status(**journey, ballot_uuid=ballot)["result_uuid"]
        issued, = discussion._rows("""SELECT p.version_id, a.actor_id FROM public.core_libpublicationfact p
                                      JOIN public.core_cqact a ON a.act_uuid=p.operation_uuid
                                      WHERE p.operation_uuid=%s AND p.kind='PUBLICATION'""", [result])
        self.assertEqual((issued["version_id"], issued["actor_id"]), (version.pk, last.pk))
        result_url = reverse("discussion_result", args=(owner.organism_id, board.circle_uuid, journey["thread_uuid"], result))
        page = reader.get(result_url)
        self.assertContains(page, "Version 2 was published.", count=1)
        self.assertContains(page, "How this result was reached")
        self.assertContains(page, MOTION)
        self.assertNotContains(page, CLAUSE)
        self.assertNotContains(page, "in force")
        self.assertFalse(any("/laws/" in a["attrs"].get("href", "") for a in Markup(page.content.decode()).select("a")))


def result_detail_fixture(board, thread, result, ballot, disposition, proof, stamp):
    """Feed the real result SQL typed PostgreSQL rows without inserting a second vote.

    Only the named relations change to WITH relations of the same composite type.
    All joins, projections and predicates come from the production query.
    """
    import re

    opening, version = uuid4(), uuid4()
    local = dict(organism_id=1, board_uuid=board, thread_uuid=thread)
    source = dict(kind="CQ_RESULT", reference=str(result))
    instrument = dict(kind="PUBLISH_POLICY", spec=dict(resource="duties", version=2), text=TEXT)
    plan = {"domain": "PUBLISH_POLICY", "resource": 10, "version": 20}
    acts = [
        dict(local, operation="INITIATE", request={"attachment": instrument}),
        dict(local, operation="OPEN_VOTE", act_uuid=ballot, facts={"version": str(version)}),
        dict(local, operation="SETTLE", act_uuid=result, recorded_at=stamp,
             facts={"ballot": str(ballot), "disposition": disposition, "domain_plan": plan if proof else None}),
    ]
    if proof:
        acts.append(dict(local, operation="IMPLEMENT", request={"result": str(result)}, authority_source=source,
                         facts={"disposition": "APPLIED", "domain_plan": plan}))
    tables = {
        "core_cqact": acts,
        "discp_posts": [dict(thread_uuid=thread, post_number=1, post_uuid=opening)],
        "core_discpostversion": [dict(post_uuid=opening, version_uuid=version, version_number=3)],
        "core_discbody": [dict(version_uuid=version, markdown=MOTION)],
        "core_libraryresource": [dict(id=10, resource_id="duties")],
        "core_libraryresourceversion": [dict(id=20, resource_id=10, version_number=2, content=TEXT)],
        "core_libraryresourcetransition": [dict(id=30, resource_id=10, version_id=20, to_state="published")],
        "core_libpublicationfact": [dict(operation_uuid=result if proof == "exact" else uuid4(),
                                        kind="PUBLICATION", organism_id=1, version_id=20,
                                        transition_id=30, authority_source=source)],
    }
    ctes = [f"{name} AS (SELECT * FROM jsonb_populate_recordset(NULL::public.{name},%s::jsonb))" for name in tables]
    query = re.sub(r"public\.(\w+)", lambda m: m[1] if m[1] in tables else m[0], cq_reads._RESULT_DETAIL)
    params = [json.dumps(rows, default=str) for rows in tables.values()]
    return discussion._rows("WITH " + ", ".join(ctes) + " " + query, [*params, board, thread, result])


class PublicationResultProjectionTests(TestCase):
    """L08-result. Exercise the public release query and real HTTP page with zero stored fixture rows."""

    def test_result_is_outcome_only_for_unseated_member(self):
        """A member with result standing only may read the motion and status, never the instrument."""
        from src.intevia.services import _record_names

        with projected_forms() as (client, org, circle, _, thread):
            result, ballot, board = uuid4(), uuid4(), uuid4()
            address = reverse("discussion_result", args=(org, circle, thread, result))
            stamp = datetime(2026, 11, 27, tzinfo=timezone.utc)
            for disposition, proof, notice in (
                ("NO_EFFECT", None, "The Policy was not published."),
                ("NO_AUTHORITY", None, "The Policy was not published."),
                ("APPLIED", "other-result", None),
                ("APPLIED", "exact", "Version 2 was published."),
            ):
                with self.subTest(disposition=disposition, proof=proof):
                    rows = result_detail_fixture(board, thread, result, ballot, disposition, proof, stamp)
                    detail = dict(post_uuid=result, effective_at=stamp, event_kind="DECISION", ballot=ballot,
                                  count={"kind": "NOT_QUALIFIED_VOTE" if disposition == "NO_AUTHORITY" else "QUALIFIED"})
                    with patch.object(discussion, "_rows", return_value=rows), \
                         patch.object(_record_names, "full_name_and_short_label", return_value=("Board of Trustees", "Board")), \
                         patch.object(cq_reads, "_rule", return_value=None):
                        cq_reads._result_detail(dict(board_uuid=board, organism_id=1, thread_audience=False), None,
                                                thread, detail)
                    with patch.object(cq_reads, "result_post", return_value=detail), \
                         patch.object(discussion_posts, "read_thread", side_effect=discussion.DiscussionUnavailable):
                        page = client.get(address)
                    self.assertNotContains(page, CLAUSE, msg_prefix="A publication result must never release the Policy clause.")
                    self.assertContains(page, MOTION)
                    self.assertNotContains(page, "Back to Topic")
                    markup = Markup(page.content.decode())
                    main, = markup.select("main")
                    links = {a["attrs"]["href"] for a in markup.select("a", within=main)}
                    self.assertLessEqual(links, {reverse("organisation_home", args=(org,)),
                                                 reverse("circle_page", args=(org, circle))},
                                         "A result-only reader gets the breadcrumb and no draft link.")
                    self.assertNotContains(page, "[#approval]")
                    if notice:
                        self.assertContains(page, notice, count=1)
                    if notice != "Version 2 was published.":
                        self.assertNotContains(page, "Version 2 was published.")
