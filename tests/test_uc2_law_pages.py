"""Box 10b. A member's law register and permanent links to released adoptions."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from core import law_pages, models
from src.intevia.services import library_laws
from tests.test_f0_ui import Markup


DUTIES = "The organisation has a Treasurer. The Board of Trustees appoints the holder with the nominee's consent."


class Headings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.levels = []

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.levels.append((tag, dict(attrs).get("class", "").split()))


class LawMarkupTests(SimpleTestCase):
    def test_marked_and_unmarked_text_on_every_law_page(self):
        """Walk both real views with one released record, including the dated-query boundary."""
        org, binding = uuid4(), uuid4()
        owner = {"organism_uuid": org, "full_name": "Alder Beck Community Trust", "short_label": "Alder Beck"}
        stamp = datetime(2026, 11, 20, 21, 2, tzinfo=timezone.utc)
        marked = "Unlabelled lead.\r\n[#approval] " + DUTIES + "\r\n  §2-b Next <line> & more.\nContinued."
        law = dict(binding=binding, issuer=org, version_number=1, library_reference="alpha-treasurer-duties",
                   display_class="Policy", content=marked, decision_route="CQ_GOVERNANCE_CIRCLE",
                   in_force_from=stamp, adoption_recorded_at=stamp)
        later_row = dict(law, binding=uuid4(), library_reference="trust-membership", version_number=3)
        mark = dict(binding=binding, reference=law["library_reference"], version_number=1,
                    title="Treasurer office", family="Governance", class_name="POLICY", display_class="Policy")
        unlabelled = dict(mark, binding=later_row["binding"], reference="trust-membership", version_number=3,
                          title="Policy", family=None)
        version_page = dict(law=law, status="In force now", display=mark, earlier=[], end_at=None, current_version=None)
        listing = reverse("organisation_laws", args=[org])
        detail = reverse("organisation_law_version", args=[org, binding, 1])
        for mode in ("light", "dark"):
            board_name = "Board of Trustees" if mode == "light" else "Fixture Board"
            for path in (listing, detail):
                with self.subTest(mode=mode, path=path):
                    request = RequestFactory().get(path, {"at": "yesterday", "known_at": "tomorrow"})
                    request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Test reader")
                    request.intevia_identity = SimpleNamespace(colour_mode=mode, display_name="Test reader")
                    match = request.resolver_match = resolve(path)
                    with patch.object(law_pages.transaction, "atomic", return_value=nullcontext()), \
                         patch.object(law_pages.member_navigation, "member_organisation_name", return_value=owner), \
                         patch.object(library_laws, "our_laws", return_value={"laws": [law, later_row]}) as read_list, \
                         patch.object(library_laws, "law_display_rows", return_value=[mark, unlabelled]), \
                         patch.object(library_laws, "_law_ranks", return_value={"POLICY": 4}), \
                         patch.object(law_pages, "adoption_disclosure", return_value=dict(
                             body=f"{board_name} · Alder Beck Community Trust", route="Board vote",
                             in_force_from=stamp, recorded=stamp)), \
                         patch.object(library_laws, "our_law_version_page", return_value=version_page) as read_one:
                        response = match.func(request, **match.kwargs)
                    page = response.content.decode()
                    self.assertNotIn("[#", page, "A reader must never see the stored bracket locator.")
                    self.assertNotRegex(page, r"(?:^|[>\r\n])[ \t]*§", "A line-start section locator must become a label.")
                    headings = Headings()
                    headings.feed(page)
                    self.assertEqual([level for level, _ in headings.levels],
                                     ["h1", "h2", "h3"] if path == listing else ["h1", "h2"],
                                     "Walk the rendered headings without skipping a level.")
                    self.assertTrue(all("record-heading" in css for tag, css in headings.levels if tag != "h3"))
                    self.assertIn(f'data-mode="{mode}"', page)
                    markup = Markup(page)
                    nav, = markup.select("nav", **{"aria-label": "Primary navigation"})
                    current_tabs = markup.select("a", within=nav, **{"aria-current": "page"})
                    self.assertEqual([(n["text"].strip(), n["attrs"]["href"]) for n in current_tabs],
                                     [("INTEVIA", reverse("f0-organisation"))],
                                     "Both law pages must keep INTEVIA as the sole current primary tab.")
                    if path == listing:
                        read_list.assert_called_once_with(credential=request.user, organism_uuid=org)
                        self.assertContains(response, f'href="{detail}"')
                        self.assertContains(response, "alpha-treasurer-duties · Version 1 · In force now")
                        self.assertContains(response, "<span><strong>Policy</strong><small>trust-membership · Version 3 · In force now</small></span>", html=True)
                        self.assertContains(response, 'class="circle-list"')
                        self.assertContains(response, '<h3 id="law-class-1">Policy</h3>', html=True)
                        divided, = markup.select("ul", classes=("circle-list",))
                        for link in markup.select("a", within=divided):
                            self.assertEqual([n["text"] for n in markup.select("span", within=link, **{"aria-hidden": "true"})],
                                             ["›"], "Each law link needs one decorative chevron.")
                        self.assertLess(page.index(law["library_reference"]), page.index(later_row["library_reference"]))
                        self.assertNotIn("The organisation has a Treasurer", page)
                    else:
                        read_one.assert_called_once_with(credential=request.user, organism_uuid=org,
                                                         binding=binding, version_number=1)
                        self.assertEqual([n["text"] for n in markup.select("h1")], ["Policy: Treasurer office"])
                        self.assertEqual(markup.select("p", classes=("meta",))[0]["text"],
                                         "alpha-treasurer-duties · Governance · Version 1 · In force now")
                        self.assertEqual([n["text"] for n in markup.select("h2")], ["Earlier versions"])
                        for text in ("Unlabelled lead.", "Clause “approval”", "The organisation has a Treasurer",
                                     "Clause “2-b”", "Next &lt;line&gt; &amp; more.\nContinued.",
                                     "Treasurer office", "Governance · Version 1 · In force now",
                                     "In force from", "Recorded", "No earlier versions.",
                                     f"{board_name} · Alder Beck Community Trust", "Board vote",
                                     'class="button secondary"', "Back to Organisation laws"):
                            self.assertIn(text, page, f"The version page must show {text!r}.")
        plain = "  Préambule.\r\nSecond paragraph.\n"
        self.assertEqual(law_pages.law_text_parts(plain), [(None, plain)], "Unmarked text must remain whole.")
        self.assertEqual(law_pages.law_text_parts("\ufeff[#a.1]\n§b-2 End"), [("a.1", ""), ("b-2", "End")])

    def test_version_card_contains_the_text_and_closed_adoption_record(self):
        """A superseded version exercises every adoption fact and the return to the current version."""
        from django.template.loader import render_to_string

        org, previous, successor = uuid4(), uuid4(), uuid4()
        since = datetime(2026, 11, 20, 14, 30, tzinfo=timezone.utc)
        end = since.replace(day=25)
        recorded = since.replace(hour=12)
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
        request.resolver_match = None
        page = Markup(render_to_string("core/organisation_law_version.html", {
            "organisation": {"organism_uuid": org, "short_label": "Alder Beck"},
            "title": "Policy", "mark": {"reference": "local-office-policy"}, "class_label": "Policy",
            "status": "Superseded", "version": 2, "parts": [("review", "The Board reviews the record.")],
            "adoption": dict(body="Board of Trustees · Alder Beck", route="Board vote",
                             in_force_from=since, end_at=end, recorded=recorded),
            "is_current": False, "current_version": {"binding": successor, "version_number": 5},
            "earlier": [{"binding": previous, "version_number": 1, "status": "Superseded",
                         "in_force_from": since}],
        }, request=request))
        card, = page.select("article", classes=("card",))
        self.assertIn("office-provision", card["attrs"]["class"].split())
        self.assertEqual([n["text"] for n in page.select("div", within=card, classes=("office-clause",))],
                         ["The Board reviews the record."])
        records = page.select("details", within=card, classes=("office-adoption",))
        self.assertEqual(len(records), 1, "Adoption record belongs inside the law card.")
        record, = records
        self.assertNotIn("open", record["attrs"], "The native disclosure must start closed.")
        summary, = page.select("summary", within=record)
        self.assertIs(summary["parents"][-1], record)
        self.assertEqual(summary["text"], "Adoption record")
        facts, = page.select("dl", within=record, classes=("office-facts",))
        terms = page.select("dt", within=facts)
        values = page.select("dd", within=facts)
        self.assertEqual(terms, page.select("dt"))
        self.assertEqual(values, page.select("dd"))
        self.assertEqual([(n["text"], v["text"].strip()) for n, v in zip(terms, values, strict=True)], [
            ("Adopting body", "Board of Trustees · Alder Beck"), ("Adoption route", "Board vote"),
            ("In force from", "20 November 2026, 14:30 UTC"), ("In force until", "25 November 2026, 14:30 UTC"),
            ("Recorded", "20 November 2026, 12:30 UTC"),
        ])
        notice, = page.select("aside", classes=("notice",))
        self.assertLess(page.nodes.index(notice), page.nodes.index(card))
        self.assertEqual(notice["text"].strip(), "This is an earlier version. Current version: Version 5")
        link, = page.select("a", within=notice)
        self.assertEqual((link["text"], link["attrs"]["href"]), ("Current version: Version 5",
                         reverse("organisation_law_version", args=[org, successor, 5])))
        self.assertFalse(page.select("section", **{"aria-labelledby": "earlier-versions"}),
                         "An earlier version must have no earlier-versions section, even with history supplied.")

    def test_current_page_lists_full_utc_date_ranges_as_flat_links(self):
        from datetime import timedelta
        from django.template.loader import render_to_string

        org, fourth, third = uuid4(), uuid4(), uuid4()
        start = datetime(2026, 11, 14, tzinfo=timezone.utc)
        end = start + timedelta(days=10)
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
        request.resolver_match = None
        page = Markup(render_to_string("core/organisation_law_version.html", {
            "organisation": {"organism_uuid": org, "short_label": "Alder Beck"},
            "title": "Policy: Treasurer office", "mark": {"reference": "alpha-treasurer-duties"},
            "is_current": True, "status": "In force now", "version": 5, "in_force_from": end, "recorded": end,
            "earlier": [{"binding": fourth, "version_number": 4, "in_force_from": start, "end_at": end},
                        {"binding": third, "version_number": 3, "in_force_from": start.replace(month=10), "end_at": start}],
        }, request=request))
        card, = page.select("article", classes=("card",))
        history, = page.select("section", **{"aria-labelledby": "earlier-versions"})
        self.assertLess(page.nodes.index(card), page.nodes.index(history))
        ul, = page.select("ul", within=history)
        self.assertEqual(ul["attrs"].get("class"), "version-list", "Use the shared flat version list.")
        self.assertFalse(any("card" in n["attrs"].get("class", "").split() for n in ul["parents"]))
        self.assertEqual([n["text"] for n in page.select("li", within=ul)], [
            "Version 4 · in force between 14 November 2026 and 24 November 2026",
            "Version 3 · in force between 14 October 2026 and 14 November 2026",
        ])
        self.assertEqual([n["attrs"]["href"] for n in page.select("a", within=ul)],
                         [reverse("organisation_law_version", args=[org, fourth, 4]),
                          reverse("organisation_law_version", args=[org, third, 3])])
        self.assertFalse(page.select("aside", classes=("notice",)))

    def test_shared_version_list_spacing_is_in_the_served_bundle(self):
        import re
        from pathlib import Path
        from django.contrib.staticfiles import finders

        css = Path(finders.find("core/f0.css")).read_text()
        rule, = re.findall(r"(?<![\w-])\.version-list\{([^}]+)\}", css)
        declarations = dict(line.split(":", 1) for line in rule.rstrip(";").split(";"))
        self.assertEqual(declarations.get("gap"), "var(--space-xl)", "Version rows need their own vertical gap.")
        self.assertEqual(declarations.get("display"), "grid")
        self.assertEqual(declarations.get("line-height"), "var(--leading)")
        self.assertEqual(declarations.get("list-style"), "none")
        content_links = (
            "main a:not(.button):not(.nav-item):not(.domain-item)"
            ":not(:where(.circle-list>li>a,.version-list a,nav a))"
        )
        self.assertIn(content_links + "{text-decoration-line:underline}", css,
                      "The content-link underline must exempt the whole version link so its dates stay plain.")
        self.assertIn(".version-list a>span:first-child{text-decoration:underline}", css,
                      "The version label keeps its own underline inside the single link.")

    def test_version_titles_and_text_come_from_the_released_law(self):
        org, binding = uuid4(), uuid4()
        stamp = datetime(2026, 11, 25, tzinfo=timezone.utc)
        page_url = reverse("organisation_law_version", args=[org, binding, 1])
        list_url = reverse("organisation_laws", args=[org])
        owner = dict(organism_uuid=org, full_name="River Trust", short_label="River")
        ref = "intevia:charter:free-will"
        first_clause = "Chosen <freely>."
        cases = (
            ("# Free Will Charter\r\n[#one] " + first_clause + "\r\n\r\n§two Kept **as written**.", "Free Will Charter"),
            ("A preamble.\n# Later heading", "Article"),
            ("\ufeff# Free Will Charter", "Free Will Charter"),
            ("# " + ref + "\nWhole text.", "Article"),
        )
        for text, title in cases:
            row = dict(binding=binding, issuer=uuid4(), class_name="ARTICLE", display_class="Article", content=text,
                       library_reference=ref, version_number=1, decision_route="UNLABELLED",
                       in_force_from=stamp, adoption_recorded_at=stamp)
            mark, = library_laws.law_display_rows(org, [row])
            for path in (page_url, list_url):
                with self.subTest(text=text[:20], path=path):
                    request = RequestFactory().get(path)
                    request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
                    match = request.resolver_match = resolve(path)
                    with patch.object(law_pages.transaction, "atomic", return_value=nullcontext()), \
                         patch.object(law_pages.member_navigation, "member_organisation_name", return_value=owner), \
                         patch.object(library_laws, "our_laws", return_value={"laws": [row]}), \
                         patch.object(library_laws, "_law_ranks", return_value={"ARTICLE": 1}), \
                         patch.object(library_laws, "our_law_version_page", return_value=dict(
                            law=row, status="In force now", display=mark, earlier=[], end_at=None, current_version=None)):
                        response = match.func(request, **match.kwargs)
                    page = Markup(response.content.decode())
                    if path == list_url:
                        link, = page.select("a", href=page_url)
                        self.assertEqual([n["text"] for n in page.select("strong", within=link)], [title],
                                         "The divided row must use the same human title as the addressed version.")
                        self.assertIn(ref, page.select("small", within=link)[0]["text"])
                        continue
                    self.assertEqual([n["text"] for n in page.select("h1")], [title if title == "Article" else f"Article: {title}"],
                                     "Only an opening heading, a marked film title or the class may title the law.")
                    self.assertTrue(any(ref in n["text"] for n in page.select("p", classes=("meta",))))
                    content, = page.select("article", classes=("card", "office-provision"))
                    self.assertNotIn("# Free Will Charter", content["text"], "A promoted title is omitted from the body.")
                    if first_clause in text:
                        self.assertEqual([n["text"] for n in page.select("div", within=content, classes=("office-clause",))],
                                         [first_clause + "\n", "Kept **as written**."], "Blank lines and literal body text survive.")
                    if title == "Article":
                        self.assertIn(text, content["text"], "Without a promoted heading the body remains whole.")
                    facts = [n["text"] for n in page.select("dd")]
                    self.assertIn("Inherited from INTEVIA", facts, "The platform law must identify its origin.")
                    self.assertIn("Adoption route unavailable", facts, "An unknown adoption route must still render.")

    def test_organisation_home_has_one_divided_laws_row_below_circles(self):
        from django.template.loader import render_to_string
        org = uuid4()
        home = reverse("organisation_home", args=[org])
        request = RequestFactory().get(home)
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
        request.resolver_match = resolve(home)
        for mode in ("light", "dark"):
            request.intevia_identity = SimpleNamespace(colour_mode=mode, display_name="Reader")
            page = Markup(render_to_string("core/organisation_home.html", {
                "organisation": dict(organism_uuid=org, full_name="River Trust"), "circles": [], "show_settings": True}, request))
            self.assertEqual([n["text"] for n in page.select("h2", classes=("record-heading",))], ["Circles", "Laws", "Settings"],
                             "An admitted home must put Laws after Circles and before Settings.")
            section, = page.select("section", **{"aria-labelledby": "laws-heading"})
            divided, = page.select("ul", within=section, classes=("circle-list",))
            row, = page.select("li", within=divided)
            link, = page.select("a", within=row)
            self.assertEqual(link["attrs"]["href"], reverse("organisation_laws", args=[org]))
            self.assertEqual([n["text"] for n in page.select("strong", within=link)], ["Organisation laws"])
            self.assertEqual([n["text"] for n in page.select("small", within=link)],
                             ["The laws in force here, and their earlier versions"])
            self.assertEqual([n["text"] for n in page.select("span", within=link, **{"aria-hidden": "true"})], ["›"])

    def test_same_label_keeps_distinct_classes_and_unranked_arrival(self):
        rows = [dict(class_name=code, display_class=label, family=None, reference=ref)
                for code, label, ref in [
                    ("TERMS_OF_REFERENCE", "Terms of reference", "a"),
                    ("BYLAW", "Bylaw", "b"),
                    ("MEMBERS_RESOLUTION", "Bylaw", "c"),
                    ("OTHER", "Other", "d"),
                ]]
        # Only the engine ranks are substituted. Both "Bylaw" labels are real
        # display inputs: an organisation can give its resolutions a local label.
        ranks = {"BYLAW": 3, "MEMBERS_RESOLUTION": 2, "TERMS_OF_REFERENCE": None, "OTHER": None}
        with patch.object(library_laws, "_law_ranks", side_effect=lambda names, alias: {n: ranks.get(n) for n in names}):
            groups = library_laws.law_class_groups(rows)
        self.assertEqual(len(groups), 4, "Two distinct classes sharing a display label must remain separate.")
        self.assertEqual([g["label"] for g in groups], ["Bylaw", "Bylaw", "Terms of reference", "Other"])
        self.assertEqual([g["rows"][0]["reference"] for g in groups], ["c", "b", "a", "d"])

    def test_film_coordinates_equal_the_real_setup(self):
        from core.management.commands import setup_alpha_trustee as setup

        for reference in (setup.DUTIES_REF, setup.MINIMUM_REF):
            self.assertEqual(library_laws._film_root(reference), setup.scenario_id("adopt-" + reference),
                             "Display lineage must end at the issued adoption for this reference.")

    def test_historic_release_must_agree_on_all_three_coordinates(self):
        """An index hit identifies the probe. A different row from that probe releases nothing."""
        from datetime import timedelta

        now = datetime(2026, 11, 25, tzinfo=timezone.utc)
        start = now - timedelta(days=2)
        binding, org = uuid4(), uuid4()
        credential = object()
        candidate = dict(reference="duties", version_number=1, probe_at=start,
                         end_kind="SUPERSEDED", end_at=now - timedelta(days=1))
        exact = dict(binding=binding, library_reference="duties", version_number=1)
        scope = dict(credential=credential, organism_uuid=org, database_alias="default")
        for change in ({}, {"binding": uuid4()}, {"version_number": 2}, {"library_reference": "different"}):
            historical = dict(exact, **change)
            with self.subTest(change=change), patch.object(library_laws.transaction, "atomic", return_value=nullcontext()), \
                 patch.object(library_laws, "_version_coordinates", return_value=candidate), \
                 patch.object(library_laws, "our_laws", side_effect=[
                     {"laws": [], "known_at": now, "at": now}, {"laws": [historical]}]) as reads:
                if change:
                    with self.assertRaises(library_laws.LawReadUnavailable):
                        library_laws.our_law_version(**scope, binding=binding, version_number=1)
                else:
                    self.assertEqual(library_laws.our_law_version(**scope, binding=binding, version_number=1),
                                     (historical, "Superseded"))
                reads.assert_called_with(**scope, at=start, known_at=now)

    def test_earlier_links_need_their_own_release_at_the_pinned_cuts(self):
        from datetime import timedelta

        now = datetime(2026, 11, 25, tzinfo=timezone.utc)
        first_start, second_start = now - timedelta(days=4), now - timedelta(days=2)
        org, first, second, latest = uuid4(), uuid4(), uuid4(), uuid4()
        selected = dict(binding=latest, library_reference="law-7", version_number=3,
                        class_name="POLICY", display_class="Policy", content="Earlier history fixture.")
        first_row = dict(selected, binding=first, version_number=1, in_force_from=first_start, effective_until=second_start)
        different = dict(selected, binding=uuid4(), version_number=2)
        candidates = [dict(reference="law-7", version_number=2, probe_at=second_start, end_kind="SUPERSEDED", end_at=now),
                      dict(reference="law-7", version_number=1, probe_at=first_start, end_kind="RETIRED", end_at=second_start)]
        current = {"at": now, "known_at": now, "laws": [selected]}
        scope = dict(credential=object(), organism_uuid=org, database_alias="default")
        with patch.object(library_laws.transaction, "atomic", return_value=nullcontext()), \
             patch.object(library_laws, "_earlier_coordinates", return_value=[(second, 2), (first, 1)]) as coordinates, \
             patch.object(library_laws, "_version_coordinates", side_effect=candidates), \
             patch.object(library_laws, "our_laws", side_effect=[current, {"laws": [different]}, {"laws": [first_row]}]) as read:
            page = library_laws.our_law_version_page(**scope, binding=latest, version_number=3)
        self.assertEqual(page["earlier"], [{"binding": first, "version_number": 1,
                                            "status": "No longer in force", "in_force_from": first_start, "end_at": second_start}],
                         "An index entry whose historic audience release misses must have no link.")
        coordinates.assert_called_once_with(latest, now, "default")
        self.assertEqual([args.kwargs for args in read.call_args_list], [
            scope, dict(scope, at=second_start, known_at=now), dict(scope, at=first_start, known_at=now)],
            "Each earlier adoption must pass the member reader at its own start and the page's knowledge cut.")

    def test_earlier_version_resolves_only_the_current_matching_law(self):
        now = datetime(2026, 11, 25, tzinfo=timezone.utc)
        org, old, latest = uuid4(), uuid4(), uuid4()
        ended = dict(binding=old, resource=21, scope="PLATFORM", issuer=org, effective_until=now,
                     version_number=2, library_reference="law-7", content="Earlier version.",
                     class_name="POLICY", display_class="Policy")
        current_row = dict(ended, binding=latest, version_number=5, predecessor=uuid4())
        others = [dict(current_row, binding=uuid4(), scope="CIRCLE:other"),
                  dict(current_row, binding=uuid4(), issuer=uuid4()),
                  dict(current_row, binding=uuid4(), resource=22)]
        for released, target in ((others + [current_row], {"binding": latest, "version_number": 5}),
                                 (others, None)):
            with self.subTest(current=target):
                with patch.object(library_laws.transaction, "atomic", return_value=nullcontext()), \
                     patch.object(library_laws, "our_laws", return_value={"known_at": now, "at": now, "laws": released}) as read, \
                     patch.object(library_laws, "_released_historical_version", return_value=(ended, "No longer in force")), \
                     patch.object(library_laws, "_earlier_coordinates") as history:
                    page = library_laws.our_law_version_page(credential=object(), organism_uuid=org,
                                                            binding=old, version_number=2)
                self.assertEqual(page["current_version"], target, "Link only to the released law in force in this scope.")
                self.assertEqual(page["earlier"], [])
                history.assert_not_called()
                read.assert_called_once()

    def test_anonymous_reader_uses_login_on_both_routes(self):
        from urllib.parse import parse_qs, urlsplit
        org = uuid4()
        for path in (reverse("organisation_laws", args=[org]),
                     reverse("organisation_law_version", args=[org, uuid4(), 1])):
            response = Client().get(path)
            self.assertEqual(response.status_code, 302, "A signed-out reader must enter through login.")
            redirect = urlsplit(response.url)
            self.assertEqual(redirect.path, reverse("login"))
            self.assertEqual(parse_qs(redirect.query).get("next"), [path])


def adopt(test, owner, reader, document):
    from src.intevia.services import law_acts
    from tests import test_law_acts as acts
    spec = acts.adoption(owner, document)
    receipt = law_acts.OrganismLawService().adopt_law(acts.command(reader, spec))
    test.assertEqual(receipt.status, "ADOPT", "The setup must adopt the published law through the service.")
    return spec


def admit_member(owner, founder, applicant):
    from src.intevia.services import organism_application_service as gateway
    from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
    from src.intevia.services.organism_authority import S015Authority
    from tests.s015_0021_support import database_now

    service = gateway.OrganismApplicationService(
        authority=S015Authority(provider=ProductionApplicationAuthorityProvider()), clock=database_now)
    result = service.submit_application(gateway.SubmitApplicationCommand(
        **command_fields(applicant), living_organism_id=owner.organism_id, answer="I would like to join."))
    service.claim_application(gateway.ClaimApplicationCommand(
        **command_fields(founder), application_uuid=result.application_uuid))
    service.decide_application(gateway.DecideApplicationCommand(
        **command_fields(founder), application_uuid=result.application_uuid, outcome="ACCEPT"))
    return models.OrganismMembership.objects.get(living_organism=owner, identity=applicant)


def command_fields(person):
    from tests.s015_0021_support import database_now
    now = database_now()
    key = uuid4().hex
    return dict(credential=person.credential, request_reference="law-page:" + key, idempotency_key=key,
                occurred_at=now, effective_at=now)


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class LawPageReleaseTests(TransactionTestCase):
    def test_first_link_survives_the_successors_exact_start(self):
        from src.intevia.services import law_acts
        from tests import test_law_acts as acts
        from tests.s015_0021_support import database_now
        from tests.test_law_records import Document
        from tests.test_uc1_office_page import signed_in

        owner, founder = acts.ordinary_birth()
        org = owner.organism_id
        scope = dict(credential=founder.credential, organism_uuid=org)
        client = signed_in(founder)
        listing = reverse("organisation_laws", args=[org])
        self.assertContains(client.get(reverse("organisation_home", args=[org])), f'href="{listing}"')
        self.assertContains(client.get(listing), "No laws were in force at this time.")
        unknown_then = database_now()
        document = Document(founder, content="[#approval] " + DUTIES)
        first = adopt(self, owner, founder, document)
        first_url = reverse("organisation_law_version", args=[org, first.proposal, 1])
        self.assertContains(client.get(first_url), "In force now")
        with self.assertRaises(library_laws.LawReadUnavailable):
            library_laws.our_law_version(**scope, binding=first.proposal, version_number=1, known_at=unknown_then)
        old_cut = database_now()
        second_version = document.next_version()
        document.transition("publish_library_resource")
        self.assertContains(client.get(first_url), "The organisation has a Treasurer")
        second = replace(first, proposal=uuid4(), predecessor=first.proposal, version=second_version.version_number)
        receipt = law_acts.OrganismLawService().adopt_law(acts.command(founder, second))
        self.assertEqual(receipt.status, "ADOPT", "The successor must be adopted before the boundary check.")
        start = models.LivingOrganismEvent.objects.get(event_uuid=receipt.event).effective_at
        at_boundary = library_laws.our_laws(**scope, at=start, known_at=database_now())["laws"]
        self.assertEqual([(r["binding"], r["version_number"]) for r in at_boundary], [(second.proposal, 2)],
                         "Exactly the successor must govern at the successor's first instant.")
        historical = client.get(first_url)
        self.assertContains(historical, "Superseded", msg_prefix="The original link must release the first adoption.")
        self.assertContains(historical, "Clause “approval”")
        self.assertContains(historical, "The organisation has a Treasurer")
        self.assertNotContains(historical, "Second version.")
        second_url = reverse("organisation_law_version", args=[org, second.proposal, 2])
        current = client.get(second_url)
        self.assertContains(current, "In force now")
        self.assertContains(current, "Second version.")
        self.assertContains(current, f'href="{first_url}"', msg_prefix="The successor must link to the first adoption.")
        self.assertContains(historical, f'href="{second_url}"', msg_prefix="The ended adoption must link to its released successor.")
        self.assertContains(historical, "In force until")
        self.assertNotContains(current, "In force until")
        page = client.get(listing, {"at": old_cut.isoformat(), "known_at": old_cut.isoformat()})
        self.assertContains(page, f'href="{second_url}"')
        self.assertNotContains(page, first_url)
        self.assertNotContains(page, "Second version.")

    def test_all_private_refusals_have_one_body(self):
        from src.intevia.services import organism_membership_service as memberships
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_authority import S015Authority
        from tests import test_law_acts as acts
        from tests.s015_0021_support import database_now, register_test_learner
        from tests.test_law_records import Document
        from tests.test_uc1_office_page import assert_private, signed_in

        owner, founder = acts.ordinary_birth()
        other, coordinator = acts.ordinary_birth()
        org = owner.organism_id
        local = adopt(self, owner, founder, Document(founder))
        foreign = adopt(self, other, coordinator, Document(coordinator))
        first = reverse("organisation_law_version", args=[org, local.proposal, 1])
        listing = reverse("organisation_laws", args=[org])
        outsider = register_test_learner(username="law-outside-" + uuid4().hex, display_name="Law")
        former = register_test_learner(username="law-former-" + uuid4().hex, display_name="Law")
        membership = admit_member(owner, founder, former)
        prior = database_now()
        former_client = signed_in(former)
        self.assertContains(former_client.get(first), "In force now")
        service = memberships.OrganismMembershipService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()), clock=database_now)
        service.leave_organism(memberships.LeaveOrganismCommand(
            **command_fields(former), membership_uuid=membership.membership_uuid, confirmed=True, reason_class="VOLUNTARY"))
        membership.refresh_from_db()
        self.assertEqual(membership.state, "ENDED")
        with self.assertRaises(library_laws.LawReadUnavailable, msg="Leaving must end even a formerly released read."):
            library_laws.our_law_version(credential=former.credential, organism_uuid=org, binding=local.proposal,
                                         version_number=1, known_at=prior)
        self.assertIsNone(library_laws._version_coordinates(org, foreign.proposal, database_now(), "default"),
                          "A foreign local adoption must never become a candidate for this organisation.")
        self.assertFalse(models.OrganismMembership.objects.filter(living_organism=owner, identity=coordinator).exists())
        member_client = signed_in(founder)
        outside_client = signed_in(outsider)
        coordinator_client = signed_in(coordinator)
        missing = uuid4()
        # All four display names have initial L, so even the personalised shell is comparable byte for byte.
        denials = [(reader, path) for reader in (outside_client, former_client, coordinator_client)
                   for path in (listing, first)]
        denials.extend((member_client, path) for path in (
            reverse("organisation_laws", args=[missing]),
            reverse("organisation_law_version", args=[missing, local.proposal, 1]),
            reverse("organisation_law_version", args=[org, uuid4(), 1]),
            reverse("organisation_law_version", args=[org, foreign.proposal, 1]),
            reverse("organisation_law_version", args=[org, local.proposal, 2])))
        baseline = outside_client.get(listing).content
        for reader, path in denials:
            with self.subTest(path=path):
                response = reader.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertTemplateUsed(response, "core/organisation_unavailable.html")
                assert_private(self, response)
                self.assertEqual(response.content, baseline, "A refused page must reveal nothing about the coordinate.")

    def test_same_published_film_reference_in_another_organisation(self):
        from src.intevia.services import law_acts, library_policy
        from core.management.commands import setup_alpha_trustee as setup
        from tests import test_law_acts as acts
        from tests.test_uc1_office_page import signed_in

        film, founder = acts.ordinary_birth()
        other, coordinator = acts.ordinary_birth()
        scope = dict(credential=founder.credential, organism_uuid=film.organism_id)
        version = library_policy.draft_policy(**scope, resource_reference=setup.DUTIES_REF, content=setup.DUTIES_TEXT,
            authors=[founder.identity_id], declare_own_authorship=True, request_uuid=uuid4())
        library_policy.publish_policy(**scope, version_id=version.pk, request_uuid=uuid4())
        original = law_acts.LawProposal(organism=film.organism_id, resource=setup.DUTIES_REF,
            proposal=setup.scenario_id("adopt-" + setup.DUTIES_REF), operation="ADOPT", intent="ADOPT",
            law_class="POLICY", version=1)
        other_adoption = replace(original, organism=other.organism_id, proposal=uuid4())
        for person, spec in ((founder, original), (coordinator, other_adoption)):
            receipt = law_acts.OrganismLawService().adopt_law(acts.command(person, spec))
            self.assertEqual(receipt.status, "ADOPT", "Both organisations must really adopt the same published resource.")
        client = signed_in(coordinator)
        page = client.get(reverse("organisation_laws", args=[other.organism_id]))
        self.assertNotContains(page, "Treasurer office", msg_prefix="A second organisation must not borrow the film title.")
        self.assertContains(page, setup.DUTIES_REF, count=1)
        self.assertNotContains(page, "Policy · Governance")
        detail = client.get(reverse("organisation_law_version", args=[other.organism_id, other_adoption.proposal, 1]))
        self.assertContains(detail, '<h1 class="record-heading page-title">Policy</h1>', html=True)
        self.assertContains(detail, "No earlier versions.")
        from src.intevia.services import office_record
        for person, spec, title in ((founder, original, "Treasurer office"), (coordinator, other_adoption, None)):
            released, _ = library_laws.our_law_version(credential=person.credential, organism_uuid=spec.organism,
                                                       binding=spec.proposal, version_number=1)
            provision = dict(binding=spec.proposal, version=1, adoption_record=released["act"],
                             library_reference=setup.DUTIES_REF, class_name="POLICY")
            office_record._label_citations(person.credential, spec.organism, [provision])
            self.assertEqual((provision["film_title"], provision["film_family"], provision["version_link"]),
                             (title, "Governance" if title else None, True),
                             "The office read must carry the mark only for the film adoption.")

    def test_engine_orders_members_resolution_between_article_and_bylaw(self):
        from src.intevia.services import law_acts
        from tests import test_law_acts as acts
        from tests.test_law_records import Document
        from tests.test_uc1_office_page import signed_in

        org, founder = acts.ordinary_birth()
        documents = sorted([Document(founder) for _ in range(3)], key=lambda doc: doc.name)
        # Reference order deliberately opposes engine order on every run.
        for doc, code in zip(documents, ("BYLAW", "MEMBERS_RESOLUTION", "ARTICLE"), strict=True):
            proposal = acts.adoption(org, doc, code)
            if code == "MEMBERS_RESOLUTION":
                proposal = replace(proposal, resolution_effect="STANDING", resolution_reference="fixture:standing-decision")
            receipt = law_acts.OrganismLawService().adopt_law(acts.command(founder, proposal))
            self.assertEqual(receipt.status, "ADOPT", "The class named by the witness must have a real law in force.")
        page = signed_in(founder).get(reverse("organisation_laws", args=[org.organism_id]))
        self.assertEqual(page.status_code, 200)
        groups = page.context["groups"]
        self.assertEqual(groups[0]["label"], "Article", "The first class must follow engine rank, ahead of Bylaw.")
        self.assertEqual([g["label"] for g in groups], ["Article", "Members' Resolution", "Bylaw"])
        self.assertContains(page, 'class="circle-list"', count=3)

    def test_retired_adoption_is_released_at_its_start_after_readoption(self):
        from src.intevia.services import law_acts
        from tests import test_law_acts as acts
        from tests.s015_0021_support import database_now
        from tests.test_law_records import Document
        from tests.test_uc1_office_page import signed_in

        org, founder = acts.ordinary_birth()
        scope = dict(credential=founder.credential, organism_uuid=org.organism_id)
        first = adopt(self, org, founder, Document(founder))
        prior = database_now()
        service = law_acts.OrganismLawService()
        retirement = replace(first, proposal=uuid4(), predecessor=first.proposal, operation="RETIRE", intent="RETIRE")
        self.assertEqual(service.adopt_law(acts.command(founder, retirement)).status, "RETIRE")
        client = signed_in(founder)
        old_url = reverse("organisation_law_version", args=[org.organism_id, first.proposal, 1])
        retired_page = client.get(old_url)
        self.assertContains(retired_page, "No longer in force", msg_prefix="An entirely retired law keeps its original address.")
        self.assertNotContains(retired_page, "Current version:", msg_prefix="A retired law may have no live adoption.")
        self.assertNotContains(retired_page, "Earlier versions")
        self.assertEqual(library_laws.our_law_version(**scope, binding=first.proposal, version_number=1,
                                                      known_at=prior)[1], "In force now")
        new = replace(first, proposal=uuid4(), predecessor=first.proposal)
        self.assertEqual(service.adopt_law(acts.command(founder, new)).status, "ADOPT")
        current = client.get(reverse("organisation_law_version", args=[org.organism_id, new.proposal, 1]))
        self.assertContains(current, f'href="{old_url}"', msg_prefix="A re-adoption must link to the earlier retired binding.")
        self.assertContains(current, "in force between", msg_prefix="A retired adoption keeps its dated history link.")
        old_page = client.get(old_url)
        self.assertNotContains(old_page, "Superseded by")
        self.assertContains(old_page, 'Current version: Version 1')
        self.assertNotContains(old_page, "Earlier versions")

    def test_only_twenty_most_recent_earlier_adoptions_are_linked(self):
        from src.intevia.services import law_acts
        from tests import test_law_acts as acts
        from tests.test_law_records import Document

        org, founder = acts.ordinary_birth()
        spec = adopt(self, org, founder, Document(founder))
        bindings = [spec.proposal]
        service = law_acts.OrganismLawService()
        # Every adoption names the same published version, but has its own
        # receipt and address. Twenty-one earlier adoptions cross the page bound.
        for _ in range(21):
            spec = replace(spec, proposal=uuid4(), predecessor=spec.proposal)
            receipt = service.adopt_law(acts.command(founder, spec))
            self.assertEqual(receipt.status, "ADOPT", "Each earlier address must identify an actual adoption.")
            bindings.append(spec.proposal)
        page = library_laws.our_law_version_page(credential=founder.credential, organism_uuid=org.organism_id,
                                                binding=spec.proposal, version_number=1)
        self.assertEqual([entry["binding"] for entry in page["earlier"]], bindings[-2:0:-1],
                         "The twenty most recent earlier adoptions must appear once each in descending order.")
        first_page = library_laws.our_law_version_page(credential=founder.credential, organism_uuid=org.organism_id,
                                                       binding=bindings[0], version_number=1)
        self.assertEqual(first_page["current_version"], {"binding": bindings[-1], "version_number": 1},
                         "The earliest adoption must return to the live binding beyond the history cap.")
        self.assertEqual(first_page["earlier"], [])


class LawTextCardTests(SimpleTestCase):
    def test_blank_unmarked_parts_create_no_clause_box(self):
        from django.template.loader import render_to_string

        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
        request.resolver_match = None
        organisation = dict(organism_uuid=uuid4(), short_label="River")
        cases = (
            ("# Title\n\n[#1] First clause.", [("1", "First clause.")]),
            ("# Title\r\n \t\r\n§2 Next.", [("2", "Next.")]),
            ("# Title\n", []),
            (" \r\n\t", []),
            ("\ufeff \t\n[#a]\n§b End", [("a", ""), ("b", "End")]),
            ("# Title\n  Introduction.  \n[#1] Text. ", [(None, "  Introduction.  "), ("1", "Text. ")]),
            ("  Whole document.\r\n", [(None, "  Whole document.\r\n")]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                _, remaining = library_laws.law_heading(source)
                parts = law_pages.law_text_parts(remaining)
                self.assertEqual(parts, expected, "Only substantive unmarked text may create a clause box.")
                page = Markup(render_to_string("core/organisation_law_version.html", {
                    "organisation": organisation, "parts": parts, "title": "Policy", "version": 1,
                }, request=request))
                card, = page.select("article", classes=("office-provision",))
                boxes = page.select("div", within=card, classes=("office-clause",))
                self.assertEqual([node["text"] for node in boxes], [text for _, text in expected])
                labels = page.select("p", within=card, classes=("meta",))
                self.assertEqual([node["text"] for node in labels],
                                 [f"Clause “{key}”" for key, _ in expected if key])


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AdoptionBodyPageTests(TransactionTestCase):
    def board_adoption(self, *, exact_pin=False):
        from src.intevia.services import circle_commands, cq, law_acts, library_policy
        from tests import cq_authority_support as boards
        from tests import cq_settings_support
        from tests.office_record_support import a1_adopt_policy, a1_definition
        from tests.test_cq_clarity import CQClarityTests
        from tests.test_discussion_office_links import record_office

        owner, scope, trustees, board = boards.ordinary(3, board=True)
        founder, officer = trustees[:2]
        reference = "board-adoption-" + uuid4().hex
        first, binding, receipt = a1_adopt_policy(owner, founder, reference, "[#approval] First duties.")
        self.assertEqual(receipt.status, "ADOPT")
        office = record_office(owner, founder, a1_definition(reference, first.version_number))

        reader = founder
        if exact_pin:
            reader = self.open_probationary_pin(owner, founder, scope, office)

        # Publish the successor while the Coordinator route is still available.
        version = library_policy.draft_policy(
            **scope, resource_reference=reference, content="[#approval] Revised duties.",
            authors=[founder.identity_id], declare_own_authorship=True, request_uuid=uuid4(),
            predecessor_version_id=first.pk)
        library_policy.publish_policy(**scope, version_id=version.pk, request_uuid=uuid4())
        cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": False},
                           request_uuid=uuid4())
        circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())
        boards.appoint_board_coordinator(owner, board, founder, officer)
        journey, _, post = CQClarityTests.topic(
            self, scope | {"credential": officer.credential, "circle_uuid": board.circle_uuid},
            title="Adopt revised Treasurer duties", markdown="Adopt the published duties.")
        proposal = law_acts.LawProposal(
            organism=owner.organism_id, resource=reference, proposal=uuid4(), predecessor=binding,
            operation="ADOPT", intent="ADOPT", law_class="POLICY", version=version.version_number)
        cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=post["version"],
                       attachment={"kind": "LAW", "spec": proposal.content(), "text": version.content})
        ballot = cq_settings_support.open_motion(journey, post["version"], trustees)
        result = cq_settings_support.settle(journey, ballot, trustees)
        self.assertEqual(result["disposition"], "APPLIED", "A real Board vote must adopt this exact successor.")

        target = reverse("organisation_law_version", args=[owner.organism_id, proposal.proposal, version.version_number])
        name = models.LOFoundingContentVersion.objects.get(living_organism=owner, version=1).name
        return owner, reader, office, target, name

    def open_probationary_pin(self, owner, founder, scope, office):
        from src.intevia.services import circle_commands, discussion, discussion_posts
        from tests.test_discussion_office_links import discussion_category, public_start_args
        from tests.test_law_acts import add_member
        from tests.test_office_record_services import fixture_membership_state
        from tests.test_uc1_office_page import office_path, signed_in

        reader = add_member(owner)
        welcome_circle = circle_commands.form(**scope, circle_uuid=uuid4(), kind="GENERIC",
                                              purpose="Welcome new members", request_uuid=uuid4())
        board = discussion_category(owner, founder, welcome_circle.circle_uuid)
        local = scope | {"circle_uuid": welcome_circle.circle_uuid}
        fixture_membership_state(owner, founder, reader, "PROBATIONARY")
        discussion.set_probation_access(**scope, enabled=True, request_uuid=uuid4())
        discussion.open_circle_welcome(**local, request_uuid=uuid4())
        welcome = next(c for c in discussion.board_categories(**local)["categories"] if c["functional_code"] == "WELCOME")
        self.assertEqual(signed_in(reader).get(office_path(owner, office)).status_code, 404,
                         "Before the Topic pins this office the probationary reader cannot open it.")
        opening = public_start_args(owner, founder, welcome_circle, board) | {"category_uuid": welcome["category_uuid"]}
        discussion_posts.start_topic(**opening, office_definition_uuid=office)
        return reader

    def adoption_facts(self, page, card):
        disclosure, = page.select("details", within=card, classes=("office-adoption",))
        return {term["text"]: value["text"].strip() for term, value in zip(
            page.select("dt", within=disclosure), page.select("dd", within=disclosure), strict=True)}

    def read_office_adoption(self, client, owner, office, target):
        from tests.test_uc1_office_page import office_path

        response = client.get(office_path(owner, office))
        self.assertEqual(response.status_code, 200)
        page = Markup(response.content.decode())
        card, = [card for card in page.select("article", classes=("office-provision",))
                 if page.select("a", within=card, href=target)]
        return self.adoption_facts(page, card), response.context["record"]["office"]

    def test_local_board_adoption_names_the_same_body_on_both_pages(self):
        from tests.test_uc1_office_page import signed_in

        owner, reader, office, target, name = self.board_adoption()
        from src.intevia.services import _record_names, office_record

        client = signed_in(reader)
        binding = resolve(target).kwargs["binding"]
        selected, _ = library_laws.our_law_version(credential=reader.credential, organism_uuid=owner.organism_id,
                                                   binding=binding, version_number=resolve(target).kwargs["version_number"])
        adopted = selected["in_force_from"]
        original_org = office_record.full_name_and_short_label
        original_board = library_laws.governance_board_names

        def org_at(*, owner_id, cut, circle=None):
            """Keep the real historical name; the resolver supplies a different name at later cuts."""
            if circle is None and cut > adopted:
                return "Later Trust", "Later"
            return original_org(owner_id=owner_id, cut=cut, circle=circle)

        def board_at(org, cut):
            """Represent a later Board name at the read boundary until governed renaming lands."""
            if cut > adopted:
                return "Later Board", "Later"
            return original_board(org, cut)

        with patch.object(_record_names, "full_name_and_short_label", side_effect=org_at), \
             patch.object(office_record, "full_name_and_short_label", side_effect=org_at), \
             patch.object(library_laws, "governance_board_names", side_effect=board_at):
            office_facts, current = self.read_office_adoption(client, owner, office, target)
            response = client.get(target)
            self.assertEqual(response.context["organisation"]["full_name"], "Later Trust")
            self.assertEqual((current["board_full_name"], current["organisation_full_name"]),
                             ("Later Board", "Later Trust"), "The page must exercise the later name state.")
        self.assertEqual(response.status_code, 200)
        page = Markup(response.content.decode())
        law_card, = page.select("article", classes=("office-provision",))
        law_facts = self.adoption_facts(page, law_card)
        self.assertEqual((office_facts["Adoption route"], law_facts["Adoption route"]), ("Board vote", "Board vote"))
        expected = f"Board of Trustees · {name}"
        self.assertEqual((office_facts["Adopting body"], law_facts["Adopting body"]), (expected, expected),
                         "Both adoption records must name the Board and its organisation at adoption.")
        from src.intevia.services import cq_reads, discussion
        for denied in ("result_post", "implementation_record"):
            with self.subTest(denied=denied), patch.object(cq_reads, denied, side_effect=discussion.DiscussionUnavailable()):
                response = client.get(target)
                self.assertEqual(response.status_code, 200)
                limited = Markup(response.content.decode())
                card, = limited.select("article", classes=("office-provision",))
                limited_facts = self.adoption_facts(limited, card)
                self.assertEqual((limited_facts["Adopting body"], limited_facts["Adoption route"]), (name, "Board vote"))
                self.assertEqual("Adoption vote" in limited_facts, denied == "implementation_record")

    def test_probationary_exact_pin_withholds_the_board_name_but_keeps_the_route(self):
        from tests.test_uc1_office_page import signed_in

        owner, reader, office, target, name = self.board_adoption(exact_pin=True)
        facts, office_read = self.read_office_adoption(signed_in(reader), owner, office, target)
        self.assertNotIn("board_full_name", office_read, "The exact pin must not admit the Board's name.")
        self.assertEqual(facts["Adoption route"], "Board vote")
        self.assertEqual(facts["Adopting body"], name, "An exact Topic pin must not restore the withheld Board name.")

    def test_original_minimum_keeps_its_interim_authority_after_activation(self):
        """L05-min. Record the issued founding minimum and a successor before a real Board activation."""
        from core.management.commands import setup_alpha_trustee as setup
        from src.intevia.services import circle_commands, law_acts, library_policy
        from tests import cq_authority_support as boards
        from tests import test_law_acts as acts
        from tests.office_record_support import a1_adopt_policy
        from tests.test_uc1_office_page import signed_in

        owner, scope, trustees, board = boards.ordinary(3, board=True)
        founder = trustees[0]
        version = library_policy.draft_policy(**scope, resource_reference=setup.MINIMUM_REF,
            content=setup.MINIMUM_TEXT, authors=[founder.identity_id], declare_own_authorship=True,
            request_uuid=uuid4(), route="COORDINATOR_ROUTE")
        library_policy.publish_policy(**scope, version_id=version.pk, request_uuid=uuid4(), route="COORDINATOR_ROUTE")
        spec = law_acts.LawProposal(organism=owner.organism_id, resource=setup.MINIMUM_REF,
            proposal=setup.scenario_id("adopt-" + setup.MINIMUM_REF), operation="ADOPT", intent="ADOPT",
            law_class="POLICY", version=version.version_number)
        receipt = law_acts.OrganismLawService().adopt_law(acts.command(founder, spec))
        self.assertEqual((receipt.status, receipt.route), ("ADOPT", "COORDINATOR_ROUTE"))
        client = signed_in(founder)
        first_url = reverse("organisation_law_version", args=[owner.organism_id, spec.proposal, 1])

        def facts_at(url):
            """Read the actual member page and its own native adoption disclosure."""
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            page = Markup(response.content.decode())
            card, = page.select("article", classes=("office-provision",))
            return self.adoption_facts(page, card)

        interim = "Interim authority before Board activation"
        self.assertNotContains(client.get(first_url), "View vote result", msg_prefix="A founding adoption has no Board vote.")
        self.assertEqual(facts_at(first_url)["Adoption route"], interim, "Only the founding adoption has interim authority.")
        # The same issued Policy text can also be adopted elsewhere under another binding.
        other, coordinator = acts.ordinary_birth()
        other_spec = replace(spec, organism=other.organism_id, proposal=uuid4())
        other_receipt = law_acts.OrganismLawService().adopt_law(acts.command(coordinator, other_spec))
        self.assertEqual(other_receipt.status, "ADOPT")
        other_url = reverse("organisation_law_version", args=[other.organism_id, other_spec.proposal, 1])
        self.assertNotContains(signed_in(coordinator).get(other_url), interim)

        later, successor, receipt = a1_adopt_policy(owner, founder, setup.MINIMUM_REF, setup.MINIMUM_TEXT + "\n",
                                                   prior_version=version, prior_binding=spec.proposal)
        self.assertEqual(receipt.status, "ADOPT")
        second_url = reverse("organisation_law_version", args=[owner.organism_id, successor, later.version_number])
        self.assertEqual(facts_at(second_url)["Adoption route"], "Organisation Coordinator decision")
        from src.intevia.services import cq
        cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": False},
                           request_uuid=uuid4())
        circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())
        historic = facts_at(first_url)
        self.assertEqual(historic["Adoption route"], interim)
        self.assertIn("In force until", historic)
        self.assertNotIn("Board vote", historic.values())


class AdoptionDisclosureTests(SimpleTestCase):
    def test_withheld_board_label(self):
        """L05-withheld. The office's grant controls Board identity even if all optional evidence would confirm it."""
        from src.intevia.services import office_record

        org = uuid4()
        at = datetime(2026, 11, 24, 19, 1, tzinfo=timezone.utc)
        law = dict(issuer=org, decision_route="CQ_GOVERNANCE_CIRCLE", in_force_from=at, adoption_recorded_at=at)
        with patch.object(models.LivingOrganism.objects, "values_list") as ids, \
             patch.object(office_record, "full_name_and_short_label", return_value=("Historic Trust", "Trust")), \
             patch.object(office_record, "_board_adoption", return_value=(True, {"body": uuid4(), "result_address": (uuid4(),) * 4}, True)) as evidence, \
             patch.object(library_laws, "governance_board_names", return_value=("Historical Board", "Board")) as names:
            ids.return_value.get.return_value = 1
            result = office_record.adoption_disclosure(object(), org, law, board_name_allowed=False)
            self.assertEqual(result["body"], "Historic Trust", "The office grant withholds the Board name.")
            self.assertEqual(result["route"], "Board vote")
            evidence.assert_not_called()
            names.assert_not_called()
            admitted = office_record.adoption_disclosure(object(), org, law, board_name_allowed=True)
            self.assertEqual(admitted["body"], "Historical Board · Historic Trust")
            names.assert_called_once_with(org, at)
            inherited = office_record.adoption_disclosure(object(), uuid4(), law, board_name_allowed=True)
            self.assertEqual(inherited["body"], "Inherited from INTEVIA")
            names.assert_called_once_with(org, at)

    def test_founding_route_requires_activation_to_be_absent_at_the_adoption_cut(self):
        """Check both cuts and the activation predicate without introducing another adoption journey."""
        from src.intevia.services import office_record

        org = uuid4()
        start = datetime(2026, 11, 24, 19, 1, tzinfo=timezone.utc)
        recorded = start.replace(minute=3)
        law = dict(issuer=org, decision_route="COORDINATOR_ROUTE", library_reference="alpha-board-minimum",
                   class_name="POLICY", scope_kind="LIVING_ORGANISM", predecessor=None, version_number=1,
                   binding=library_laws._film_root("alpha-board-minimum"),
                   in_force_from=start, adoption_recorded_at=recorded)
        with patch.object(models.LivingOrganism.objects, "values_list") as ids, \
             patch.object(office_record, "full_name_and_short_label", return_value=("River Trust", "River")), \
             patch.object(models.CircleStateEvent.objects, "filter") as activations, \
             patch.object(office_record, "_board_adoption") as votes:
            ids.return_value.get.return_value = 7
            activations.return_value.exists.return_value = True
            facts = office_record.adoption_disclosure(object(), org, law, board_name_allowed=True)
            self.assertEqual(facts["route"], "Organisation Coordinator decision")
            activations.assert_called_once_with(circle__parent_organism_id=7, circle__kind="GOVERNANCE",
                action="ACTIVATE", effective_at__lte=start, received_at__lte=recorded, recorded_at__lte=recorded)
            activations.return_value.exists.return_value = False
            facts = office_record.adoption_disclosure(object(), org, law, board_name_allowed=True)
            self.assertEqual((facts["body"], facts["route"]), ("River Trust", "Interim authority before Board activation"))
            inherited = office_record.adoption_disclosure(object(), uuid4(), law, board_name_allowed=True)
            self.assertEqual((inherited["body"], inherited["route"]), ("Inherited from INTEVIA", "Organisation Coordinator decision"))
            votes.assert_not_called()
