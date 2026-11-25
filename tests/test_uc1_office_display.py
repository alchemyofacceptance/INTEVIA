"""HTTP witnesses for when a member opening the office can present a CARE observation."""
from html import unescape
from typing import NamedTuple
from unittest.mock import patch
from uuid import UUID, uuid4

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.signals import template_rendered

from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, office_path, signed_in


class OfficeHistory(NamedTuple):
    details: list[UUID]
    observations: list[UUID]
    events: list[UUID]
    head: UUID


@override_settings(ALLOWED_HOSTS=["testserver"])
class OfficeDisplayTests(TransactionTestCase):
    def setUp(self):
        from tests.office_record_support import approved_office, read_source_candidate
        from tests.test_law_acts import add_member

        self.owner, self.founder, self.office, _, _, _ = approved_office()
        self.reader = add_member(self.owner)
        self.client = signed_in(self.reader)
        self.path = office_path(self.owner, self.office)
        self.peer = connection.copy()
        self.addCleanup(self.peer.close)
        self.source = read_source_candidate(self.owner, self.reader, self.office)
        self.assertTrue(self.source["may_create"], "The active member and approved vacant office must qualify before the request.")
        self.before = self.history()
        self.assertEqual((self.before.details, self.before.observations), ([], []),
                         "Every first-display witness needs an office with no observation detail or event.")

    def history(self):
        # One peer statement sees one committed cut, including any unrelated view event.
        with self.peer.cursor() as cursor:
            cursor.execute("""
                SELECT ARRAY(SELECT event_uuid FROM public.core_careofficeobservation
                              WHERE organism_id=o.id ORDER BY event_uuid),
                       ARRAY(SELECT event_uuid FROM public.core_livingorganismevent
                              WHERE living_organism_id=o.id AND action='RECORD_CARE_OFFICE_OBSERVATION'
                              ORDER BY sequence),
                       ARRAY(SELECT event_uuid FROM public.core_livingorganismevent
                              WHERE living_organism_id=o.id ORDER BY sequence), h.event_uuid
                  FROM public.core_livingorganism o
                  JOIN public.core_livingorganismevent h ON h.id=o.head_event_id WHERE o.id=%s
            """, [self.owner.pk])
            return OfficeHistory(*cursor.fetchone())

    def assert_only_new_pair(self, after, before):
        self.assertEqual(len(after.details), 1, "The eligible office opening must commit exactly one observation detail.")
        self.assertEqual(after.observations, after.details, "The committed ILC observation must have the detail's event UUID.")
        self.assertEqual((after.events, after.head), (before.events + after.details, after.details[0]),
                         "The observation must be the only new organisation entry and must be its head.")

    def set_off(self):
        from src.intevia.services.care_assistance import set_assistance

        key = uuid4()
        set_assistance(credential=self.founder.credential, organism_uuid=self.owner.organism_id,
                       enabled=False, predecessor=None, request_uuid=key)
        return key

    def test_only_a_real_get_can_present(self):
        from src.intevia.services import office_record as service
        from tests.test_law_acts import add_member
        from tests.test_office_record_services import fixture_membership_state

        probes = (
            ("HEAD", {}, "A HEAD request must leave the observation and ILC pair absent."),
            ("GET", {"Sec-Purpose": "prefetch"}, "A Sec-Purpose-prefetch request must leave the pair absent."),
            ("GET", {"Sec-Purpose": "PREFETCH;prerender"}, "A compound speculative request must leave the pair absent."),
            ("GET", {"Purpose": "prefetch"}, "A Purpose-prefetch request must leave the observation and ILC pair absent."),
            ("GET", {"Purpose": "navigate", "Sec-Purpose": "prefetch"}, "Sec-Purpose must prevail over an ordinary Purpose."),
            ("GET", {"Purpose": "prefetch", "Sec-Purpose": "navigate"}, "Purpose must prevail over an ordinary Sec-Purpose."),
        )
        with patch.object(service, "office_record", wraps=service.office_record) as read:
            for method, headers, message in probes:
                with self.subTest(method=method, headers=headers):
                    response = getattr(self.client, method.lower())(self.path, headers=headers)
                    self.assertEqual(self.history(), self.before, message)
                    self.assertIs(read.call_args.kwargs["present_care"], False, "A speculative read must call the facts path.")
                    self.assertEqual(response.status_code, 200, "The admitted reader must receive a successful facts response.")
                    assert_private(self, response)
                    self.assertNotIn(self.source["sentence"].encode(), response.content, "Preview must omit the observation words.")
                    if method == "HEAD":
                        self.assertEqual(response.content, b"", "A HEAD response must have an empty body.")
            restricted = add_member(self.owner)
            fixture_membership_state(self.owner, self.founder, restricted, "RESTRICTED")
            restricted_client = signed_in(restricted)
            before_get = self.history()
            limited = restricted_client.get(self.path)
            self.assertContains(limited, "Vacant", msg_prefix="The read-only member must be able to see the vacancy.")
            self.assertNotContains(limited, self.source["sentence"], msg_prefix="A read-only member cannot create first guidance.")
            self.assertEqual(self.history(), before_get, "An admitted member without creation authority must leave the history intact.")
            first = self.client.get(self.path)
            self.assertIs(read.call_args.kwargs["present_care"], True, "A genuine GET must request presentation.")
            self.assertContains(first, self.source["sentence"], msg_prefix="The first genuine GET after previews must display guidance.")
            after = self.history()
            self.assert_only_new_pair(after, before_get)
            for _, headers, _ in probes[1:]:
                preview = self.client.get(self.path, headers=headers)
                self.assertIs(read.call_args.kwargs["present_care"], False, "A stored pair cannot turn a prefetch into a presentation.")
                self.assertFalse(Markup(preview.content.decode()).select("aside", classes=("office-observation",)),
                                 "A prefetch must omit the live card even when a stored observation exists.")
            repeat = self.client.get(self.path, headers={"Purpose": "not-prefetch"})
            self.assertContains(repeat, self.source["sentence"], msg_prefix="An unrelated purpose token must allow genuine reuse.")
            self.assertEqual(self.history(), after, "Repeated requests must preserve the same committed pair and organisation head.")

    def test_first_get_publishes_the_committed_guidance(self):
        at_render = []

        def observe(sender, template, **kwargs):
            if template.name == "core/office_page.html":
                at_render.append(self.history())

        template_rendered.connect(observe, weak=False)
        try:
            first = self.client.get(self.path)
            repeat = self.client.get(self.path)
        finally:
            template_rendered.disconnect(observe)
        for response in (first, repeat):
            self.assertEqual(response.status_code, 200, "A qualified first or repeat display must serve the office page.")
            assert_private(self, response)
            page = Markup(response.content.decode())
            pairs = page.select("div", classes=("office-source-pair",))
            self.assertEqual(len(pairs), 1, "The eligible first page-open must show the newly committed observation beside its source.")
            children = [node for node in page.nodes if node["parents"] and node["parents"][-1] is pairs[0]]
            self.assertEqual([n["tag"] for n in children], ["blockquote", "aside"], "The observation must immediately follow its clause.")
            clause, aside = children
            self.assertTrue(self.source["clause_text"].startswith(" "), "The stored source must exercise the leading-space case.")
            self.assertEqual(clause["text"], self.source["clause_text"].strip(),
                             "The displayed approval clause must have no outer whitespace and preserve its full wording.")
            self.assertIn(self.source["sentence"], [p["text"] for p in page.select("p", within=aside)],
                          "The observation must give the qualified source's exact words.")
            self.assertEqual([p["text"].strip() for p in page.select("p", within=aside, classes=("meta",))],
                             [f'Source: clause \u201c{self.source["locator"]}\u201d, '
                              f'version {self.source["library_version_number"]}'],
                             "The observation must name its own source clause in quotation marks, and its own version.")
            provision = pairs[0]["parents"][-1]
            self.assertEqual([n["text"] for n in page.select("h4", within=provision, classes=("card-heading",))], ["Establishes this office"],
                             "The unmapped provision must show its human relationship.")
            self.assertEqual([n["text"] for n in page.select("p", within=provision, classes=("meta",))
                              if n["parents"][-1] is provision],
                             [f'{self.source["resource_reference"]} · Clause “{self.source["locator"]}” · Version {self.source["library_version_number"]}'],
                             "The provision must name its own quoted locator and source version.")
            self.assertNotIn(str(self.reader.identity_id).encode(), response.content,
                             "The office page must not expose the first requester's identity reference.")
            self.assertTrue(self.reader.display_name.strip(), "The privacy control needs a named requester.")
            self.assertNotIn(self.reader.display_name, aside["text"], "The observation must not name the requester.")
        after = self.history()
        self.assert_only_new_pair(after, self.before)
        self.assertEqual(at_render, [after, after], "The same pair must already be visible to a peer as each template starts rendering.")
        with self.peer.cursor() as cursor:
            cursor.execute("""SELECT sentence, clause_text, locator, library_version_number
                              FROM public.core_careofficeobservation WHERE organism_id=%s AND event_uuid=%s""",
                           [self.owner.pk, after.head])
            self.assertEqual(cursor.fetchone(), tuple(self.source[k] for k in ("sentence", "clause_text", "locator", "library_version_number")),
                             "The displayed text and source must agree with the independently committed detail.")
        key = self.set_off()
        switched = self.history()
        self.assertEqual((switched.head, switched.events), (key, after.events + [key]), "The human Off switch must be the next entry.")
        off = self.client.get(self.path)
        self.assertNotContains(off, self.source["sentence"], msg_prefix="Turning Off after display must omit stored guidance.")
        self.assertEqual(self.history(), switched, "The Off read must preserve the human switch, the earlier detail and its event.")

    def test_optional_failure_still_serves_facts(self):
        from tests.office_record_support import observation_footprint

        oracle = self.client.get(self.path, headers={"Purpose": "prefetch"})
        staged = []

        def fail_history(execute, statement, params, many, context):
            if "o.library_version_id,o.adoption_uuid" in str(statement):
                staged.append((observation_footprint(self.owner), self.history()))
                return execute("SELECT 1/0", None, False, context)
            return execute(statement, params, many, context)

        with connection.execute_wrapper(fail_history):
            fallback = self.client.get(self.path)
        clause = self.source["clause_text"].strip()
        self.assertTrue(fallback.status_code == 200 and clause in unescape(fallback.content.decode()),
                        "A failed optional presentation must still serve the human clause.")
        assert_private(self, fallback)
        self.assertEqual(len(staged), 1, "The failure must occur once inside the optional observation history read.")
        writer, peer = staged[0]
        self.assertTrue(len(writer[0]) == 1 and writer[0] == writer[1], "A genuine provisional detail and event must exist before failure.")
        self.assertEqual(peer, self.before, "The provisional pair must be invisible on the independent connection.")
        self.assertEqual(self.history(), self.before, "Rollback and facts delivery must leave the complete organisation history intact.")
        self.assertNotIn(self.source["sentence"].encode(), fallback.content, "No provisional words may escape in the facts response.")
        self.assertEqual(Markup(fallback.content.decode()).select("main")[0]["text"], Markup(oracle.content.decode()).select("main")[0]["text"],
                         "A fresh facts-only fallback must retain all the human content of the preview.")
        healthy = self.client.get(self.path)
        self.assertContains(healthy, self.source["sentence"], msg_prefix="A later healthy opening must still be able to present.")
        self.assert_only_new_pair(self.history(), self.before)

        # A working presenter must still defer to the qualifier when the office source changes.
        from core.management.commands.setup_alpha_trustee import DUTIES_TEXT
        from src.intevia.services.organism_role_definition_service import DefinitionContent, DefinitionProvision
        from tests.office_record_support import a1_adopt_policy, a1_definition
        from tests.test_discussion_office_links import record_office

        resource = "office-display-duties-" + uuid4().hex
        exception = " During a vacancy the Board approves payments."
        version, _, receipt = a1_adopt_policy(self.owner, self.founder, resource, DUTIES_TEXT + exception)
        self.assertEqual(receipt.status, "ADOPT", "The changed approval clause must be genuinely adopted.")
        revised = record_office(self.owner, self.founder, a1_definition(resource, version.version_number), prior=self.office)
        revised_cut = self.history()
        changed = self.client.get(office_path(self.owner, revised))
        self.assertContains(changed, exception.strip(), msg_prefix="The reader must receive the changed human clause.")
        self.assertFalse(Markup(changed.content.decode()).select("aside", classes=("office-observation",)),
                         "A changed clause cannot present the old live guidance.")
        self.assertEqual(self.history(), revised_cut, "A different current clause must not add an observation or view event.")
        missing_basis = DefinitionProvision("OFFICE_BASIS", "library:legacy#approval", "v1")
        missing = record_office(self.owner, self.founder, DefinitionContent(code="TREASURER", provisions=(missing_basis,)), prior=revised)
        missing_cut = self.history()
        empty = self.client.get(office_path(self.owner, missing))
        self.assertContains(empty, "No provisions to show here.", msg_prefix="A missing binding must leave the neutral provision part.")
        self.assertFalse(Markup(empty.content.decode()).select("aside", classes=("office-observation",)),
                         "An office without a readable current clause cannot present guidance.")
        self.assertEqual(self.history(), missing_cut, "An absent law source must leave the office history intact.")

    def test_first_off_read_emits_no_guidance(self):
        key = self.set_off()
        before = self.history()
        self.assertEqual((before.details, before.observations, before.events, before.head),
                         ([], [], self.before.events + [key], key), "Off-first must start with the human switch and no observation.")
        oracle = self.client.get(self.path, headers={"Sec-Purpose": "prefetch"})
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200, "An eligible member must still receive the office when CARE is Off.")
        assert_private(self, response)
        page = Markup(response.content.decode())
        words = self.source["sentence"]
        attributes = [value for node in page.nodes for value in node["attrs"].values() if value]
        self.assertFalse(any(words in value for value in attributes), "An Off page must contain no observation text in accessible attributes.")
        self.assertNotIn(words.encode(), response.content, "The first Off response must omit the guidance from all HTML.")
        self.assertFalse(any(words in value for value in response.headers.values()), "The first Off response must omit guidance from headers.")
        self.assertFalse(page.select("aside", classes=("office-observation",)), "Off must omit the observation card itself.")
        main = page.select("main")[0]["text"]
        self.assertIn(self.source["clause_text"].strip(), main, "An Off choice must preserve the human source clause.")
        self.assertEqual(main, Markup(oracle.content.decode()).select("main")[0]["text"], "An Off display must agree with the facts-only read.")
        self.assertEqual(self.history(), before, "Off-first must create neither an observation nor a page-view entry.")
