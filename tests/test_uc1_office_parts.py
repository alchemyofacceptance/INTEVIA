"""Office provisions, source placement and separately permitted Topics and decisions."""
from datetime import datetime, timedelta, timezone
from html import unescape
from types import SimpleNamespace
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, office_path, signed_in


class OfficePartsTests(SimpleTestCase):
    def setUp(self):
        self.owner = uuid4()
        self.at = datetime(2026, 11, 26, 14, 30, tzinfo=timezone.utc)
        self.office = dict(organism_uuid=self.owner, organisation_full_name="River Trust", organisation_short_label="RT",
                           board_full_name="Board of Trustees", board_short_label="Board", label="Treasurer",
                           requested_version=2, current_version=2)
        self.current = dict(relationship="OFFICE_BASIS", status="in_force_now", locator="approval",
                            clause="  The Board may approve a payment.\nKeep its receipt.", version=2,
                            adopting_body=self.owner, adoption_record=uuid4(), binding=uuid4(), adoption_route="Board vote",
                            in_force_from=self.at, recorded_at=self.at + timedelta(minutes=6), adoption_result_link=uuid4())
        self.earlier = {**self.current, "status": "cited_at_definition", "clause": "The Treasurer approves payments.",
                        "version": 1, "adoption_record": uuid4(), "binding": uuid4(), "adoption_result_link": uuid4(),
                        "in_force_from": self.at - timedelta(days=1), "adoption_route": "Organisation Coordinator decision"}
        self.observation = dict(label="CARE", words="Compare the approval clause with the vacancy.",
                                source_version=2, source_locator="approval", source_link=uuid4(), event_ref=uuid4())
        self.topic = dict(title="CARE and the Board <minutes>", created_at=self.at, office_version=1, thread_link=uuid4())
        self.page = dict(office=self.office, care={"enabled": True, "observation": self.observation},
                         provisions=[self.earlier, self.current], topics=[self.topic], tenure=[], decisions=[],
                         history=[{"label": "History-only fixture text"}])

    def render(self, *, mode="light", **changes):
        path = reverse("office_page", args=[self.owner, uuid4()])
        request = RequestFactory().get(path)
        request.user = SimpleNamespace(is_authenticated=True)
        request.intevia_identity = SimpleNamespace(display_name="Reader Example", access_state="active", colour_mode=mode)
        request.resolver_match = resolve(path)
        return render_to_string("core/office_page.html", {"record": self.page | changes}, request=request)

    def one(self, page, tag, *, within=None, **attrs):
        nodes = page.select(tag, within=within, **attrs)
        self.assertEqual(len(nodes), 1, f"The fixture must render exactly one matching {tag} element.")
        return nodes[0]

    def facts(self, page, card):
        keys = [n["text"].strip() for n in page.select("dt", within=card)]
        values = [n["text"].strip() for n in page.select("dd", within=card)]
        self.assertEqual(len(keys), len(values), "Every displayed fact must have its own value.")
        self.assertEqual(len(keys), len(set(keys)), "A card must give one value per fact label.")
        return dict(zip(keys, values))

    def test_current_law_precedes_collapsed_predecessor(self):
        """S01. Keep open current law ahead of its closed predecessor within its own group."""
        policy = {"tier": "Policy", "library_reference": "duties"}
        bylaw = {"tier": "Bylaw", "library_reference": "constitution"}
        rows = [self.earlier | policy, self.earlier | bylaw, self.current | policy,
                self.current | bylaw, self.current | policy | {"clause": "Peer clause."}]
        for part, expected in (
            (rows, [(self.current["clause"], "article"), ("Peer clause.", "article"),
                    (self.earlier["clause"], "details"), (self.current["clause"], "article"),
                    (self.earlier["clause"], "details")]),
            ([self.current | policy | {"status": "cited_and_in_force"}], [(self.current["clause"], "article")]),
            ([self.earlier | policy], [(self.earlier["clause"], "details")]),
        ):
            with self.subTest(size=len(part), status=part[0]["status"]):
                page = Markup(self.render(provisions=part))
                cards = [n for n in page.nodes if "office-provision" in n["attrs"].get("class", "").split()]
                self.assertEqual([(self.one(page, "blockquote", within=c)["text"], c["tag"]) for c in cards],
                                 expected, "The current article must precede its collapsed predecessor.")
                self.assertFalse(any("open" in c["attrs"] for c in cards if c["tag"] == "details"))

    def test_each_returned_part_reaches_only_its_own_markup(self):
        raw = self.render()
        page = Markup(raw)
        old = self.one(page, "details", classes=("office-provision",))
        latest = self.one(page, "article", classes=("office-provision",))
        self.assertEqual(self.one(page, "blockquote", within=old)["text"], self.earlier["clause"],
                         "The original citation must show its own exact clause.")
        self.assertEqual(self.one(page, "blockquote", within=latest)["text"], self.current["clause"],
                         "The in-force quotation must preserve its own complete text and line breaks.")
        self.assertNotIn("open", old["attrs"], "The earlier version must start as a native disclosure.")
        for card, row in ((old, self.earlier), (latest, self.current)):
            with self.subTest(version=row["version"]):
                fields = self.facts(page, card)
                self.assertEqual((fields["Adoption record"], fields["Adoption result"], fields["Adoption route"],
                                  fields["Adopting body"]),
                                 (str(row["adoption_record"]), str(row["adoption_result_link"]), row["adoption_route"], "River Trust"),
                                 "Each version must retain its own adoption, route and result under the organisation's full name.")
                self.assertEqual([n["attrs"]["datetime"] for n in page.select("time", within=card)],
                                 [row[key].isoformat() for key in ("in_force_from", "recorded_at")],
                                 "Each version must show its own in-force and recorded instants.")
        topic = self.one(page, "article", classes=("office-topic",))
        self.assertEqual(self.one(page, "h3", within=topic)["text"], self.topic["title"], "The released Topic title must be verbatim.")
        self.assertEqual(self.one(page, "time", within=topic)["attrs"]["datetime"], self.topic["created_at"].isoformat(),
                         "The Topic must show the recorded opening time supplied by the office read.")
        self.assertIn("Office version 1", topic["text"], "The Topic must retain its own pinned office version.")
        self.assertFalse(page.select("minutes"), "Angle brackets in a human title must remain text.")
        self.assertNotIn("History-only fixture text", raw, "A history row must supply no law, Topic or decision card.")

        no_law = self.render(provisions=[])
        self.assertIn("No provisions to show here.", no_law, "A withheld law must use the ordinary empty state.")
        self.assertNotIn(str(self.current["adoption_record"]), no_law, "Withholding law must also withhold its adoption reference.")
        self.assertNotIn(self.observation["words"], no_law, "Without the released clause no observation may appear.")
        self.assertIn(self.topic["title"], Markup(no_law).select("main")[0]["text"],
                      "Withholding law must preserve an independently permitted Topic.")
        no_topic = self.render(topics=[])
        self.assertIn("No Topics to show here.", no_topic, "An empty Topic list must have a neutral message.")
        self.assertNotIn("CARE and the Board", no_topic, "A withheld title must occur nowhere in the response.")
        self.assertIn(self.current["clause"], no_topic, "Withholding a Topic must preserve independently permitted law.")

    def test_observation_needs_one_matching_current_office_basis(self):
        raw = self.render()
        page = Markup(raw)
        pair = self.one(page, "div", classes=("office-source-pair",))
        children = [n for n in page.nodes if n["parents"] and n["parents"][-1] is pair]
        self.assertEqual([n["tag"] for n in children], ["blockquote", "aside"],
                         "The guidance must immediately follow its own quotation in reading order.")
        self.assertEqual(children[0]["text"], self.current["clause"], "The guidance must belong to the current matching text.")
        self.assertEqual(raw.count(self.observation["words"]), 1, "The exact supplied observation must appear once.")
        self.assertEqual([p["text"].strip() for p in page.select("p", within=children[1], classes=("meta",))],
                         ["Source: clause \u201capproval\u201d, version 2"],
                         "The guidance must name its own clause in quotation marks, and its own version.")
        self.assertEqual(self.one(page, "h2", within=self.one(page, "article", classes=("office-provision",)))["text"],
                         "Establishes this office",
                         "The unmapped provision heading must give its relationship.")
        # A clause identifier is shown as text, whatever characters it holds.
        awkward = 'a<b>&"c'
        escaped = self.render(provisions=[self.current | {"locator": awkward}],
                              care={"enabled": True, "observation": self.observation | {"source_locator": awkward}})
        self.assertIn("Source: clause \u201ca&lt;b&gt;&amp;&quot;c\u201d, version 2", escaped,
                      "The source line must escape its clause identifier.")
        self.assertIn('<p class="meta">Clause “a&lt;b&gt;&amp;&quot;c” · Version 2</p>', escaped,
                      "The provision metadata must escape its quoted clause identifier.")
        self.assertFalse(Markup(escaped).select("b"), "A clause identifier must never create an HTML element.")
        for label, changes in (
            ("ambiguous", {"provisions": [self.current, dict(self.current)]}),
            ("wrong version", {"provisions": [self.current | {"version": 3}]}),
            ("wrong locator", {"provisions": [self.current | {"locator": "other"}]}),
            ("whole document", {"provisions": [{k: v for k, v in self.current.items() if k != "locator"}]}),
            ("wrong relationship", {"provisions": [self.current | {"relationship": "ALTERNATIVE_ROUTE"}]}),
            ("historical only", {"provisions": [self.current | {"status": "cited_at_definition"}]}),
            ("facts only", {"care": {"enabled": True}}),
            ("Off", {"care": {"enabled": False, "observation": self.observation}}),
        ):
            with self.subTest(case=label):
                withheld = self.render(**changes)
                self.assertNotIn(self.observation["words"], withheld, "An unmatched, ambiguous or withheld observation must leave no words.")
                self.assertNotIn('aria-label="CARE observation"', withheld, "Withheld guidance must leave no accessible card.")
                self.assertIn(self.current["clause"], withheld, "Omitting guidance must preserve the human quotation.")
                self.assertNotIn("Source: clause", withheld, "Withheld guidance must leave no source line.")
                if "provisions" not in changes:
                    self.assertIn("<h2>Establishes this office</h2>", withheld,
                                  "Without CARE's guidance the human provision must keep its heading.")

    def test_inherited_whole_document_keeps_its_own_adoption(self):
        foreign = uuid4()
        charter = {k: v for k, v in self.current.items() if k not in ("locator", "adoption_route", "adoption_result_link")}
        charter.update(adopting_body=foreign, clause="First charter line.\nSecond charter line.", status="cited_and_in_force")
        for kind, label in (("OFFICE_BASIS", "Establishes this office"), ("APPOINTING_AUTHORITY", "Appointing authority"),
                            ("ALTERNATIVE_ROUTE", "Alternative route"), ("NOMINATION_ROUTE", "Nomination route"),
                            ("PARTICIPATION_REQUIRED", "Required participation"), ("EX_OFFICIO_SEAT", "Seat held by virtue of office")):
            with self.subTest(kind=kind):
                raw = self.render(provisions=[charter | {"relationship": kind}])
                page = Markup(raw)
                card = self.one(page, "article", classes=("office-provision",))
                self.assertEqual(self.facts(page, card)["Adopting body"], "Inherited from another organisation",
                                 "An inherited provision must identify its origin with the neutral wording.")
                self.assertNotIn(str(foreign), raw, "The other organisation's identifier must occur nowhere in this response.")
                self.assertEqual(self.one(page, "h2", within=card)["text"], label,
                                 "A whole document must have its own relationship heading without an invented locator.")
                self.assertEqual(self.one(page, "p", within=card, classes=("meta",))["text"], "Version 2",
                                 "A whole-document citation must omit the clause segment.")
                self.assertEqual(self.one(page, "blockquote", within=card)["text"], charter["clause"],
                                 "The whole-document citation must preserve its complete text.")
                self.assertEqual(set(self.facts(page, card)), {"Adopting body", "Adoption record", "In force from", "Recorded"},
                                 "Missing route and result permissions must remove their labels and values.")
                self.assertIn("Cited and in force", card["text"], "A combined citation must display its combined status once.")

    def test_law_section_names_the_office_and_film_policy(self):
        film = self.current | {"tier": "Policy", "library_reference": "alpha-treasurer-duties", "version": 1}
        page = Markup(self.render(provisions=[film], care={"enabled": False}))
        section = self.one(page, "section", **{"aria-labelledby": "office-provisions"})
        self.assertEqual(self.one(page, "h2", within=section, id="office-provisions")["text"],
                         "Constitutional Law related to this office", "Use the ruled section heading.")
        self.assertEqual([n["text"] for n in page.select("h3", within=section)], ["Policy · Governance"],
                         "The film Policy must identify its Governance family.")
        card = self.one(page, "article", within=section)
        self.assertEqual(self.one(page, "h2", within=card)["text"], "Treasurer office")
        self.assertEqual(self.one(page, "p", within=card, classes=("meta",))["text"],
                         "Establishes this office · Clause “approval” · Version 1")

    def test_law_groups_keep_arrival_order_and_unknown_rows_stand_alone(self):
        def row(tier, reference, words):
            return self.current | {"tier": tier, "library_reference": reference, "clause": words}

        rows = [row("Bylaw", "local-a", "First bylaw."), row("Policy", "local-b", "Other policy."),
                row(None, "unclassified", "Unknown tier."), row("Bylaw", "local-c", "Second bylaw.")]
        page = Markup(self.render(provisions=rows, care={"enabled": False}))
        section = self.one(page, "section", **{"aria-labelledby": "office-provisions"})
        groups = page.select("section", within=section)
        self.assertEqual([n["text"] for n in page.select("h3", within=section)], ["Bylaw", "Policy"],
                         "Each known tier gets one heading in first-seen order, with no guessed family or tier.")
        self.assertEqual([[n["text"] for n in page.select("blockquote", within=g)] for g in groups],
                         [["First bylaw.", "Second bylaw."], ["Other policy."]])
        cards = page.select("article", within=section)
        self.assertEqual([self.one(page, "h2", within=c)["text"] for c in cards], ["Establishes this office"] * 4)
        self.assertEqual([self.one(page, "p", within=c, classes=("meta",))["text"] for c in cards],
                         ["Clause “approval” · Version 2"] * 4, "An unmapped law must use the untitled layout.")
        ungrouped = [self.one(page, "blockquote", within=c)["text"] for c in cards if c["parents"][-1] is section]
        self.assertEqual(ungrouped, ["Unknown tier."], "The unknown tier must sit outside every named group.")

        film = row("Policy", "alpha-treasurer-duties", "Film policy.")
        mixed = Markup(self.render(provisions=[film, rows[1]], care={"enabled": False}))
        section = self.one(mixed, "section", **{"aria-labelledby": "office-provisions"})
        self.assertEqual([n["text"] for n in mixed.select("h3", within=section)], ["Policy"],
                         "A shared tier heading must not assign the film family to an unmapped neighbour.")
        self.assertEqual([h["text"] for card in mixed.select("article", within=section, classes=("office-provision",))
                          for h in mixed.select("h2", within=card)],
                         ["Treasurer office", "Establishes this office"], "Only the mapped law gets a title.")

    def test_later_reader_can_distinguish_the_two_results(self):
        adoption_id, payment_id = uuid4(), uuid4()
        adoption = dict(purpose="standing_adoption", label="adopted the Policy in force", outcome="Carried.", body=uuid4(),
                        effective_at=self.at, recorded_at=self.at + timedelta(minutes=3),
                        result_uuid=adoption_id, result_link=adoption_id, adopted_version=2, adopted_binding=uuid4())
        payment = dict(purpose="one_payment", label="cited by the Policy in force", outcome="Carried.", body=adoption["body"],
                       effective_at=self.at - timedelta(hours=1), recorded_at=self.at - timedelta(minutes=55),
                       result_uuid=payment_id, result_link=payment_id, reporting_rule_link=payment_id)
        for mode in ("light", "dark"):
            for rows in ([adoption, payment], [payment], [adoption], []):
                with self.subTest(mode=mode, purposes=[r["purpose"] for r in rows]):
                    raw = self.render(mode=mode, decisions=rows)
                    page = Markup(raw)
                    self.assertEqual(self.one(page, "html")["attrs"].get("data-mode"), mode, "Use the saved mode for this result pair.")
                    cards = page.select("article", classes=("office-decision",))
                    self.assertEqual(len(cards), len(rows), "Only independently released vote results may produce decision cards.")
                    for card, row in zip(cards, rows):
                        fields = self.facts(page, card)
                        if row["purpose"] == "one_payment":
                            self.assertEqual(fields.get("Result reference"), str(payment_id), "The one-payment card must carry the payment’s own result.")
                            self.assertEqual(self.one(page, "h3", within=card)["text"], "One payment", "The payment must be described as one payment.")
                            self.assertNotIn("Adopted provision reference", fields, "The payment must not acquire the adoption's binding.")
                            self.assertEqual(fields.get("Reporting rule reference"), str(payment_id), "The payment must keep its allowed reporting reference.")
                        else:
                            self.assertEqual(fields.get("Result reference"), str(adoption_id), "The adoption must carry its own settled result.")
                            self.assertEqual(self.one(page, "h3", within=card)["text"], "Adoption of the standing Policy",
                                             "The adoption card must identify the standing Policy.")
                            self.assertEqual(fields.get("Adopted provision reference"), str(adoption["adopted_binding"]),
                                             "The standing result must carry its own adopted binding.")
                            self.assertIn("Version 2", card["text"], "The standing result must name the adopted version.")
                            self.assertNotIn("Reporting rule reference", fields, "A result without reporting permission must omit that field.")
                        self.assertIn(row["label"][0].upper() + row["label"][1:], card["text"],
                                      "The result must explain its own relationship to the Policy.")
                        self.assertIn(row["outcome"], card["text"], "The result must display its returned outcome.")
                        self.assertEqual(fields.get("Deciding body"), "Board", "A Board vote must name the deciding body by short label.")
                        self.assertNotIn(str(row["body"]), raw, "The Board identifier must not substitute for its name.")
                        self.assertEqual([n["attrs"]["datetime"] for n in page.select("time", within=card)],
                                         [row[key].isoformat() for key in ("effective_at", "recorded_at")],
                                         "Each result must retain its own effective and recorded times.")
                    if not rows:
                        self.assertIn("No decisions to show here.", raw, "A permitted provision with an adoption reference must not fabricate a result.")


@override_settings(ALLOWED_HOSTS=["testserver"])
class BoardTopicPageTests(TransactionTestCase):
    def test_board_topic_is_visible_only_in_its_readers_audience(self):
        from src.intevia.services import circle_commands, cq, discussion_posts
        from tests import cq_authority_support as boards
        from tests.test_discussion_office_links import discussion_category, office_basis, public_start_args, record_office
        from tests.test_law_acts import add_member

        owner, scope, trustees, board = boards.ordinary(3, board=True)
        founder, coordinator, trustee = trustees
        office = record_office(owner, founder, office_basis(owner, founder))
        cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": False}, request_uuid=uuid4())
        circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())
        boards.appoint_board_coordinator(owner, board, founder, coordinator)
        category = discussion_category(owner, coordinator, board.circle_uuid)
        reader = signed_in(trustee)
        outside = signed_in(add_member(owner))
        url = office_path(owner, office)

        def read(client):
            response = client.get(url)
            self.assertEqual(response.status_code, 200, "A member may open the office even when no Board Topic is released.")
            assert_private(self, response)
            return response.content.decode()

        def topics(raw):
            page = Markup(raw)
            sections = page.select("section", **{"aria-labelledby": "office-topics"})
            self.assertEqual(len(sections), 1, "The office page must have one Topics section.")
            return sections[0]["text"].strip()

        baseline = topics(read(outside))
        self.assertIn("No Topics to show here.", baseline, "The no-Topic control must have the neutral empty state.")
        self.assertEqual(topics(read(reader)), baseline, "A seated participant must have the same empty state before the opening.")
        title = "Board <minutes> on the Treasurer's duties"
        args = public_start_args(owner, coordinator, board, category) | {"title": title}
        receipt = discussion_posts.start_topic(**args, office_definition_uuid=office)

        permitted = read(reader)
        self.assertIn(title, topics(permitted), "A seated Board participant must see the released Board Topic title.")
        self.assertFalse(Markup(permitted).select("minutes"), "The Board Topic title must remain text on the real page.")
        withheld = read(outside)
        self.assertEqual(topics(withheld), baseline, "A member without a Board place must see the same empty part as before the Topic.")
        self.assertNotIn(title, unescape(withheld), "A withheld Board title must leave no trace anywhere in the response.")
        for key in ("thread", "post"):
            self.assertNotIn(str(receipt[key]), withheld, "A member outside the Board must not receive the hidden Topic's identifiers.")
        self.assertIn("The Treasurer keeps the accounts.", withheld, "Withheld discussion must preserve the member's permitted provision.")

        discussion_posts.set_post_hidden(credential=coordinator.credential, organism_uuid=owner.organism_id,
                                         circle_uuid=board.circle_uuid, category_uuid=args["category_uuid"],
                                         thread_uuid=receipt["thread"], post_uuid=receipt["post"],
                                         request_uuid=uuid4(), hidden=True, as_coordinator=True)
        hidden = read(reader)
        self.assertEqual(topics(hidden), baseline, "The participant must lose a hidden opening without a count or placeholder.")
        self.assertNotIn(title, unescape(hidden), "A hidden opening must leave no title in auxiliary markup.")
        self.assertIn(title, topics(read(signed_in(coordinator))),
                      "The authorised Board Coordinator must retain the visible moderation control.")
