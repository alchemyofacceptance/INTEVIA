"""Office history at the presentation boundary. The service supplies the order and the audience."""
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from uuid import UUID, uuid4

from django.contrib.staticfiles import finders
from django.template import Context, Template
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from tests.test_f0_ui import Markup
from tests.test_uc1_render import contrast, declarations
from tests.test_uc1_office_page import assert_private, office_path, signed_in


ILC_NOTICE = "View in ILC — not yet available"


class TimelineTests(SimpleTestCase):
    def setUp(self):
        self.at = datetime(2026, 11, 26, 16, 45, tzinfo=timezone.utc)
        self.template = Template("{% load f0_ui %}{% office_timeline record %}")

    def entry(self, kind, label, **fields):
        event = fields.get("ilc_link", uuid4())
        source = event if kind in ("decision", "setting_change") else uuid4()
        return dict(kind=kind, label=label, at=self.at, source_link=source,
                    ilc_link=event, ilc_label="View in ILC") | fields

    def render(self, history, **parts):
        record = {"office": {"current_version": 77}, "care": {"enabled": True}, "history": history} | parts
        return self.template.render(Context({"record": record}))

    def sentences(self, raw):
        page = Markup(raw)
        return [p["text"] for item in page.select("li") for p in page.select("p", within=item)]

    def test_law_adoption_names_only_its_own_version(self):
        entry = self.entry("law_adoption", "Office provision adopted")
        match = dict(binding=entry["source_link"], adoption_record=entry["ilc_link"], version=3)
        other = match | {"adoption_record": uuid4(), "version": 81}
        foreign = match | {"binding": uuid4(), "version": 92}
        raw = self.render([entry], provisions=[other, foreign, match])
        self.assertEqual(self.sentences(raw), ["Version 3 of the office provision came into force."],
                         "The history sentence must use the version belonging to this adoption.")
        self.assertEqual(self.sentences(self.render([entry], provisions=[other, foreign])), ["The office provision came into force."],
                         "An unmatched adoption must not borrow a neighbouring provision version.")

    def test_adoption_vote_names_the_adopted_version(self):
        entry = self.entry("decision", "Board vote carried")
        match = dict(result_uuid=entry["ilc_link"], result_link=entry["source_link"], purpose="standing_adoption",
                     label="adopted the Policy in force", adopted_version=2)
        other = match | {"result_uuid": uuid4(), "adopted_version": 8}
        raw = self.render([entry], decisions=[other, match])
        self.assertEqual(self.sentences(raw), ["The Board voted to adopt version 2 of the Policy in force."],
                         "The adoption vote must name the version its own result adopted.")
        self.assertEqual(self.sentences(self.render([entry], decisions=[other])), ["The Board carried the vote."],
                         "The history action alone cannot identify a version or turn it into a payment.")

    def test_payment_uses_its_own_purpose(self):
        entry = self.entry("decision", "Board vote carried")
        payment = dict(result_uuid=entry["ilc_link"], result_link=entry["source_link"], purpose="one_payment",
                       label="cited by the Policy in force")
        adoption = payment | {"result_link": uuid4(), "result_uuid": uuid4(), "purpose": "standing_adoption",
                              "label": "adopted the Policy in force", "adopted_version": 2}
        raw = self.render([entry], decisions=[adoption, payment])
        self.assertEqual(self.sentences(raw), ["The Board approved the one payment cited by the Policy in force."],
                         "The payment history must describe the one payment identified by its own result.")
        self.assertNotIn("version 2", raw, "The payment row must not inherit the standing adoption's version.")

    def test_history_links_follow_each_released_result(self):
        """L12-history. Different result receipts and a withheld sibling can share a timestamp."""
        org, board = uuid4(), uuid4()
        history, decisions, expected = [], [], []
        for purpose in ("one_payment", "standing_adoption", "publication", "withheld"):
            address = (org, board, uuid4(), uuid4())
            if purpose == "publication":
                entry = self.entry("publication", "Policy version published", version=2, result_address=address)
            else:
                entry = self.entry("decision", "Board vote carried", ilc_link=address[-1])
                if purpose != "withheld":
                    decisions.append(dict(purpose=purpose, label="cited by the Policy in force", adopted_version=2,
                                          result_uuid=address[-1], result_link=address[-1], result_address=address))
            history.append(entry)
            expected.append([] if purpose == "withheld" else [reverse("discussion_result", args=address)])
        page = Markup(self.render(history, decisions=list(reversed(decisions))))
        urls = [[a["attrs"]["href"] for a in page.select("a", within=row)] for row in page.select("li")]
        self.assertEqual(urls, expected, "L12-history exact destinations")
        self.assertNotIn(str(history[-1]["ilc_link"]), page.select("ol")[0]["text"])

    def test_topic_uses_the_verbatim_title(self):
        title = "CARE & Board <minutes> — Do we need £84?"
        entry = self.entry("topic", "Topic started: " + title)
        raw = self.render([entry], topics=[dict(thread_link=uuid4(), title="Unrelated opening"),
                                           dict(thread_link=entry["source_link"], title=title)])
        self.assertEqual(self.sentences(raw), [title], "The Topic sentence must preserve the complete returned title.")
        self.assertEqual([n["text"] for n in Markup(raw).select("strong")], [entry["label"]],
                         "The bold action must preserve the opening's delivered label.")
        self.assertFalse(Markup(raw).select("minutes"), "Angle brackets in a human title must remain human text.")

    def test_setting_history_reports_each_human_choice(self):
        on = self.entry("setting_change", "CARE assistance turned On", enabled=True)
        off = self.entry("setting_change", "CARE assistance turned Off", enabled=False)
        raw = self.render([off, on], care={"enabled": False})
        self.assertEqual(self.sentences(raw), ["CARE assistance was turned off.", "CARE assistance was turned on."],
                         "Each human switch sentence must use the setting recorded at that change.")
        page = Markup(raw)
        self.assertEqual([n["text"] for n in page.select("strong")], [off["label"], on["label"]],
                         "Turning CARE Off must keep both human switch actions.")
        self.assertFalse(page.select("span", classes=("office-care-label",)), "A human switch must not carry CARE authorship.")

    def test_observation_uses_its_stored_words_and_source(self):
        words = 'Earlier <guidance> & its "wording".'
        entry = self.entry("care_observation", "CARE", words=words, source_locator="original-source", source_version=4)
        live = dict(words="Different current guidance.", source_locator="different", source_version=7)
        raw = self.render([entry], care={"enabled": True, "observation": live})
        self.assertEqual(self.sentences(raw), [words], "A CARE history row must show its own stored words verbatim.")
        page = Markup(raw)
        self.assertEqual([n["text"] for n in page.select("span", classes=("office-care-label",))], ["CARE"],
                         "The observation needs an explicit visible CARE label.")
        self.assertEqual([n["text"].strip() for n in page.select("span", classes=("office-timeline-note",))],
                         ["Source: clause \u201coriginal-source\u201d, version 4", ILC_NOTICE],
                         "Historic guidance must keep its own stored clause and version, in its own words.")
        self.assertNotIn("\u201cdifferent\u201d", raw, "The current observation's clause must not appear in a history row.")
        self.assertFalse(page.select("guidance"), "Stored words must not create HTML elements.")
        self.assertNotIn(live["words"], raw, "Today's observation must not replace the historic sentence.")
        stored_only = self.render([entry], care={"enabled": True}, provisions=[])
        self.assertEqual(self.sentences(stored_only), [words],
                         "An independently readable historical observation needs no current live card or current clause.")
        self.assertIn("Source: clause \u201coriginal-source\u201d, version 4", stored_only,
                      "A history row must show its stored source with no current card or current clause.")
        awkward = self.entry("care_observation", "CARE", words="Stored guidance.", source_locator='a<b>&"c', source_version=4)
        self.assertIn("Source: clause \u201ca&lt;b&gt;&amp;&quot;c\u201d, version 4", self.render([awkward]),
                      "A history row must escape its stored clause identifier.")
        off = self.render([entry], care={"enabled": False, "observation": live})
        self.assertEqual(Markup(off).select("li"), [], "Off must omit a CARE row even if a stale input still contains it.")
        self.assertNotIn("Source: clause", off, "Off must leave no source line.")
        self.assertEqual(off, self.render([], care={"enabled": False}), "A filtered history must have the same markup as an empty one.")

    def test_timeline_keeps_the_delivered_order_including_ties(self):
        # These two tied rows follow the service's descending ILC reference order.
        law = self.entry("law_adoption", "Office provision adopted", ilc_link=UUID(int=12))
        vote = self.entry("decision", "Board vote carried", ilc_link=UUID(int=13))
        newest = self.entry("setting_change", "CARE assistance turned Off", enabled=False, at=self.at + timedelta(microseconds=1))
        oldest = self.entry("topic", "Topic started: Earlier", at=self.at - timedelta(days=1))
        history = [newest, vote, law, oldest]
        page = Markup(self.render(history))
        lists = page.select("ol", classes=("office-timeline",))
        self.assertEqual(len(lists), 1, "Office history must be one ordered list in the reading order.")
        items = [n for n in page.select("li") if n["parents"][-1] is lists[0]]
        self.assertEqual([n["text"] for item in items for n in page.select("strong", within=item)],
                         [e["label"] for e in history], "Timeline ties must keep the sequence delivered by the office read.")
        times = [n for item in items for n in page.select("time", within=item)]
        self.assertEqual([n["attrs"].get("datetime") for n in times], [e["at"].isoformat() for e in history],
                         "Each action must retain its own exact instant, including fractions of a second.")
        self.assertEqual(times[0]["text"], times[2]["text"], "The control needs distinct instants in the same displayed minute.")
        self.assertIn("UTC", times[0]["text"], "The readable timestamp must name its time zone.")

    def test_history_has_no_enterer(self):
        chooser, private_id = uuid4(), uuid4()
        observation = dict(words="Recorded guidance.", source_locator="approval", source_version=1)
        history = [self.entry(kind, label, enterer=private_id, enterer_name="Private fixture name",
                              requester_id=private_id, **extras) for kind, label, extras in (
            ("law_adoption", "Office provision adopted", {}), ("decision", "Board vote carried", {}),
            ("topic", "Topic started: Minutes", {}), ("setting_change", "CARE assistance turned On", {"enabled": True}),
            ("care_observation", "CARE", observation))]
        raw = self.render(history, care={"enabled": True, "switched_by": chooser, "switched_by_name": "Named fixture chooser"})
        self.assertFalse(any(value in raw for value in (str(chooser), str(private_id), "Private fixture name", "Named fixture chooser")),
                         "The history row must contain action and time without an Enterer.")
        self.assertEqual(len(Markup(raw).select("li")), len(history), "Privacy must preserve all five admitted history kinds.")
        self.assertFalse(any(word in raw.lower() for word in ("enterer", "requester", "switched_by")),
                         "History must omit attribution field names from text, attributes, comments and hidden markup.")

    def test_ilc_destination_is_unavailable(self):
        history = [self.entry("law_adoption", "Office provision adopted"),
                   self.entry("setting_change", "CARE assistance turned Off", enabled=False)]
        raw = self.render(history)
        page = Markup(raw)
        timeline, = page.select("ol", classes=("office-timeline",))
        self.assertFalse(page.select("a", within=timeline), "The interim ILC notice must have no destination.")
        self.assertFalse(any(n["tag"] in ("button", "input") or
                             any(k in n["attrs"] for k in ("href", "tabindex", "onclick", "contenteditable"))
                             for n in page.nodes if any(p is timeline for p in n["parents"])),
                         "The timeline entries must not introduce a keyboard stop or interactive stand-in.")
        size_form, = page.select("form", classes=("office-history-size",))
        outside_actions = []
        for node in page.nodes:
            if any(parent is timeline for parent in node["parents"]):
                continue
            tag, attrs = node["tag"], node["attrs"]
            outside_actions.extend(sorted({"href", "tabindex", "onclick", "contenteditable"}.intersection(attrs)))
            in_size_form = any(parent is size_form for parent in node["parents"])
            if in_size_form and tag == "select":
                outside_actions.append(("select", attrs.get("name")))
            elif in_size_form and tag == "button":
                outside_actions.append(("button", attrs.get("type"), node["text"].strip()))
            elif (tag in ("a", "button", "input", "select", "textarea", "details", "summary", "iframe")
                  or (tag in ("audio", "video") and "controls" in attrs)):
                outside_actions.append(tag)
        self.assertCountEqual(outside_actions, [("select", "show"), ("button", "submit", "Apply")],
                              "Outside the entries, the history may offer only the size choice and its Apply button.")
        items = page.select("li")
        self.assertEqual(len(items), len(history), "Both permitted entries must have their own interim notice.")
        for item, entry in zip(items, history):
            children = [n for n in page.nodes if n["parents"] and n["parents"][-1] is item]
            last = children[-1]
            self.assertEqual((last["tag"], last["text"]), ("span", ILC_NOTICE), "Every entry must end with the same plain ILC notice.")
            self.assertEqual(item["text"].count(ILC_NOTICE), 1, "The interim notice must occur exactly once per entry.")
            self.assertEqual(set(last["attrs"]), {"class"}, "The interim notice must have no focus, action, target or hidden data.")
            self.assertFalse(any(p["tag"] in ("a", "button") or "tabindex" in p["attrs"] or "onclick" in p["attrs"]
                                 for p in last["parents"]), "No ancestor may turn the interim notice into a control.")
            for key in ("ilc_link", "source_link"):
                self.assertNotIn(str(entry[key]), raw, "Internal history references must not appear anywhere in timeline markup.")


    def test_path_line_and_markers_match_the_foundation(self):
        css = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        row = declarations(css, ".office-timeline>li")
        marker = declarations(css, ".office-timeline>li:before")
        end = declarations(css, ".office-timeline>li:last-child")
        cap = declarations(css, ".office-timeline>li:last-child:after")
        self.assertEqual((marker.get("width"), marker.get("height"), marker.get("border-radius")), ("9px", "9px", "50%"),
                         "Each timeline marker must be the foundation's nine-pixel circle.")
        self.assertEqual((row.get("border-inline-start"), row.get("padding-inline-start")),
                         ("1px solid var(--color-control)", "28px"), "The timeline needs a one-pixel line and 28-pixel text inset.")
        self.assertEqual((marker.get("top"), marker.get("inset-inline-start"), marker.get("background")),
                         ("7px", "-5px", "var(--color-accent)"), "Every marker must sit on its line and follow the mode's accent.")
        self.assertIn(end.get("border-inline-start-color"), ("transparent", "#0000"), "The line must stop at the final marker.")
        self.assertEqual((cap.get("height"), cap.get("border-inline-start")), (marker["top"], row["border-inline-start"]),
                         "The last entry needs only the short line down to its circle.")
        pager = declarations(css, ".office-history-pages")
        self.assertEqual((pager.get("justify-content"), pager.get("margin-inline-start")), ("center", "36px"),
                         "The pager must centre on the entry text, 36 pixels in from the list edge.")
        number = declarations(css, ".office-entry-number")
        self.assertEqual(number.get("color"), "var(--color-accent)",
                         "Entry numbers must use the timeline’s teal accent.")
        label = declarations(css, ".office-care-label")
        self.assertEqual((label.get("color"), label.get("background")),
                         ("var(--color-accent)", "var(--color-accent-soft)"), "The CARE label must use its two approved colour roles.")
        for selector in (":root,:host", "html[data-mode=dark]"):
            palette = declarations(css, selector)
            self.assertGreaterEqual(contrast(palette["--color-accent"], palette["--color-accent-soft"]), 4.5,
                                    "The CARE label must retain readable contrast in each saved mode.")


@override_settings(ALLOWED_HOSTS=["testserver"])
class TimelinePageTests(TransactionTestCase):
    def test_off_keeps_human_content_verbatim(self):
        from core import models
        from core.management.commands.setup_alpha_trustee import DUTIES_TEXT
        from src.intevia.services import circle_commands, cq, discussion_posts
        from src.intevia.services.care_assistance import set_assistance
        from tests import cq_authority_support as boards
        from tests.office_record_support import a1_adopt_policy, a1_definition, observation_footprint, read_source_candidate
        from tests.test_discussion_office_links import discussion_category, public_start_args, record_office

        owner, scope, trustees, board = boards.ordinary(3, board=True)
        founder, coordinator, reader = trustees
        resource = "timeline-duties-" + uuid4().hex
        version, _, receipt = a1_adopt_policy(owner, founder, resource, DUTIES_TEXT)
        self.assertEqual(receipt.status, "ADOPT", "The timeline needs a real adopted Treasurer provision.")
        office = record_office(owner, founder, a1_definition(resource, version.version_number))
        cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": False}, request_uuid=uuid4())
        circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())
        boards.appoint_board_coordinator(owner, board, founder, coordinator)
        category = discussion_category(owner, coordinator, board.circle_uuid)
        title = 'Does the Board agree with CARE about the <repair> & its cost?'
        args = public_start_args(owner, coordinator, board, category) | {"title": title}
        discussion_posts.start_topic(**args, office_definition_uuid=office)
        source = read_source_candidate(owner, reader, office)
        self.assertTrue(source["may_create"], "On must start with an eligible reader and a qualifying office.")
        self.assertEqual(observation_footprint(owner)[:2], ([], []), "The first On visit needs an office without a prior observation.")
        url = office_path(owner, office)
        client = signed_in(reader)

        def read():
            response = client.get(url)
            self.assertEqual(response.status_code, 200, "The bound member must be able to read the office in either setting.")
            assert_private(self, response)
            page = Markup(response.content.decode())
            lists = page.select("ol", classes=("office-timeline",))
            self.assertEqual(len(lists), 1, "The live office page must include its permitted history as one timeline.")
            rows = []
            for item in page.select("li", within=lists[0]):
                actions = page.select("strong", within=item)
                sentences = page.select("p", within=item)
                times = page.select("time", within=item)
                self.assertEqual((len(actions), len(sentences), len(times)), (1, 1, 1),
                                 "Every history entry needs one bold action, one sentence and one time.")
                self.assertIn(ILC_NOTICE, item["text"], "Each native entry must carry the unavailable ILC notice.")
                is_care = bool(page.select("span", within=item, classes=("office-care-label",)))
                rows.append((actions[0]["text"], sentences[0]["text"], times[0]["attrs"]["datetime"], is_care))
            return response, rows

        _, on = read()
        self.assertIn(f"Version {version.version_number} of the office provision came into force.",
                      [sentence for _, sentence, _, _ in on],
                      "The real adoption must identify its fixture provision version in the timeline sentence.")
        words = source["sentence"]
        self.assertEqual([sentence for _, sentence, _, care in on if care], [words],
                         "The On timeline must show the recorded CARE words with a visible CARE label.")
        self.assertIn(title, [sentence for _, sentence, _, _ in on], "The Topic must actually be in this reader's permitted history.")
        detail_ids, event_ids, _ = observation_footprint(owner)
        self.assertTrue(len(detail_ids) == 1 and detail_ids == event_ids, "The On control must commit a matched observation pair.")
        key = uuid4()
        set_assistance(**scope, enabled=False, predecessor=None, request_uuid=key)
        off, rows = read()
        self.assertEqual([sentence for action, sentence, _, _ in rows if action == "Topic started: " + title], [title],
                         "The human’s complete Topic title must survive the switch.")
        self.assertEqual([r for r in rows if r[0] != "CARE assistance turned Off"], [r for r in on if not r[3]],
                         "Off must preserve the exact human actions, sentences, times and order.")
        self.assertEqual([(r[1], r[3]) for r in rows if r[0] == "CARE assistance turned Off"],
                         [("CARE assistance was turned off.", False)], "The Off switch must remain a human history entry.")
        raw = off.content.decode()
        page = Markup(raw)
        self.assertFalse(any(words in (v or "") for node in page.nodes for v in node["attrs"].values()),
                         "Off must omit CARE's words from every HTML attribute, including accessible names.")
        self.assertNotIn(words, unescape(raw), "Off must omit CARE's words from all returned HTML, including comments and hidden content.")
        self.assertFalse(any(words in value for value in off.headers.values()), "Off must omit CARE's words from response headers.")
        fragment = "this clause does not say who approves them"
        self.assertIn(fragment, words, "The fragment control must belong to the genuine stored sentence.")
        self.assertNotIn(fragment, source["clause_text"], "The guidance fragment must be distinct from the permitted human clause.")
        for response in (off, client.get(url, headers={"Sec-Purpose": "prefetch"}), client.head(url)):
            self.assertEqual(response.status_code, 200, "Each admitted Off read must keep the office available.")
            assert_private(self, response)
            channels = [unescape(response.content.decode()), *response.headers.values(), response.cookies.output()]
            self.assertFalse(any(token in value for token in (fragment, str(detail_ids[0])) for value in channels),
                             "Off must omit even a distinctive guidance fragment or observation pointer from every response channel.")
        self.assertNotIn(str(detail_ids[0]), raw, "Off must not carry a hidden reference to the observation.")
        self.assertFalse(page.select("span", classes=("office-care-label",)), "Off must remove the history's CARE authorship label.")
        switch_head = models.LivingOrganismEvent.objects.get(living_organism=owner, event_uuid=key).pk
        self.assertEqual(observation_footprint(owner), (detail_ids, event_ids, switch_head),
                         "The Off display must preserve the stored pair and leave the human switch at the organisation head.")
