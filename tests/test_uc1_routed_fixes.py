"""Routed U-C1 witnesses for the CARE card, visual references and public contracts."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve, reverse

from core import cache
from core.models import OrganismRoleDefinitionProvision
from core.templatetags.f0_ui import _PROVISION_LABELS
from tests.test_f0_ui import Markup
from tests.test_uc1_render import declarations
from tools import f0_theme_reference as theme


class RoutedFixTests(SimpleTestCase):
    def test_ancestors_obey_navigation_grants(self):
        """S02. An office label alone never supplies a destination."""
        from src.intevia.services import member_navigation
        org, board, office = uuid4(), uuid4(), uuid4()
        request = RequestFactory().get(reverse("office_page", args=(org, office)))
        request.resolver_match = resolve(request.path)
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
        org_url = reverse("organisation_home", args=(org,))
        board_url = reverse("circle_page", args=(org, board))
        directory = {"organisation": {"organism_uuid": org},
                     "circles": [{"kind": "GOVERNANCE", "circle_uuid": board}]}
        for released, allowed, has_board in (
            (directory, [org_url, board_url], True),
            (directory | {"circles": []}, [org_url], True),
            (None, [], True),
            (directory, [org_url], False),
        ):
            facts = {"label": "Treasurer", "organisation_short_label": "Alder Beck", "organism_uuid": org}
            if has_board:
                facts["board_short_label"] = "Board"
            with self.subTest(allowed=allowed, board=has_board), patch(
                "src.intevia.services.office_record.office_record", return_value={"office": facts}
            ), patch.object(member_navigation, "member_navigation", return_value=released,
                            side_effect=member_navigation.NavigationUnavailable() if released is None else None):
                result = request.resolver_match.func(request, **request.resolver_match.kwargs)
                page = Markup(result.content.decode())
                crumb, = page.select("nav", **{"aria-label": "Breadcrumb"})
                wide, = page.select("span", within=crumb, classes=("wide-path",))
                phone, = page.select("span", within=crumb, classes=("phone-path",))
                self.assertEqual([a["attrs"]["href"] for a in page.select("a", within=wide)], allowed)
                parent = board_url if has_board else org_url
                self.assertEqual([a["attrs"]["href"] for a in page.select("a", within=phone)],
                                 [parent] if parent in allowed else [])
                chevrons = [n for n in page.select("span", within=phone) if n["text"].strip() == "‹"]
                self.assertTrue(chevrons and all(n["attrs"].get("aria-hidden") == "true" for n in chevrons),
                                "The phone chevron is decoration and must be hidden from screen readers.")
                current, = page.select("span", within=wide, **{"aria-current": "page"})
                self.assertEqual(current["text"], "Treasurer")
                full, = page.select("span", within=phone, classes=("sr-only",))
                path = "Alder Beck › Board › Treasurer" if has_board else "Alder Beck › Treasurer"
                self.assertEqual(full["text"].strip(), "Full path: " + path + ".")
                self.assertFalse(page.select("a", within=full))
                self.assertEqual(bool(page.select("a", classes=("org-chip",))), bool(allowed))

    def test_record_heading_preserves_governed_names(self):
        """S03. Governed case, one teal page title and the history section's own name."""
        owner = {"full_name": "Alder BeCk Trust", "short_label": "Alder", "organism_uuid": uuid4()}
        circle = {"full_name": "Board of TruStees", "short_label": "Board", "kind": "GOVERNANCE"}
        for template, context, expected in (
            ("organisation_home", {"organisation": owner}, owner["full_name"]),
            ("circle_page", {"organisation": owner, "circle": circle}, circle["full_name"]),
            ("office_page", {"record": {"office": {"label": "TreasuRer"}}}, "TreasuRer"),
        ):
            with self.subTest(template=template):
                path = reverse("f0-organisation")
                request = RequestFactory().get(path)
                request.resolver_match = resolve(path)
                request.user = SimpleNamespace(is_authenticated=False)
                page = Markup(render_to_string("core/" + template + ".html", context, request))
                title, = page.select("h1")
                self.assertEqual((title["text"], set(title["attrs"]["class"].split())),
                                 (expected, {"page-title", "record-heading"}))
                for card in page.select("section", classes=("card",)):
                    self.assertFalse(page.select("h2", within=card, classes=("record-heading",)))
                if template == "office_page":
                    self.assertEqual([h["text"] for h in page.select("h2", classes=("record-heading",))],
                                     ["Constitutional Law related to this office", "Discussion topics about this office",
                                      "Decisions", "The path here"])
                    self.assertFalse(any(n["text"].strip() == "Office history" for n in page.nodes))
        sheet = Path(finders.find("core/f0.css")).read_text()
        style = declarations(sheet, ".record-heading")
        self.assertEqual(style.get("text-transform"), "none", "A governed name must retain its case.")
        self.assertEqual(style.get("color"), "var(--color-accent)")
        self.assertIn("24px", style["font"])
        self.assertIn("var(--font-heading)", style["font"])
        desktop = sheet.partition("@media (min-width:64rem){")[2].partition("@media ")[0]
        self.assertTrue(desktop, "The desktop media block must be found before it is checked.")
        self.assertNotIn("font-size", declarations(desktop, "h1"),
                         "The unlayered desktop h1 rule must exclude governed record titles.")
        self.assertEqual(declarations(sheet, ".page-title.record-heading").get("margin"), "22px 0 var(--space-sm)")
        self.assertEqual(declarations(sheet, ".record-section").get("padding-block-start"), "22px")

    def test_shared_action_buttons_keep_their_roles_and_spacing(self):
        request = RequestFactory().get(reverse("f0-you"))
        request.resolver_match = resolve(request.path)
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
        page = Markup(render_to_string("core/you.html", {}, request))
        buttons = {b["text"].strip(): b["attrs"]["class"].split() for b in page.select("button")}
        self.assertIn("primary", buttons["Save appearance"])
        self.assertIn("secondary", buttons["Confirm sign out"])
        sheet = Path(finders.find("core/f0.css")).read_text()
        self.assertIn(".button-row:has(>.button),.office-switch-actions:has(form+form){gap:2rem}", sheet)


    def read_card(self, *, enabled=True, chosen=False, coordinator=True):
        org, office = uuid4(), uuid4()
        request = RequestFactory().get(reverse("office_page", args=(org, office)))
        request.resolver_match = match = resolve(request.path)
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Current reader")
        setting = {"enabled": enabled, "can_change": coordinator, "choice_source": "chosen" if chosen else "default"}
        if chosen:
            setting.update(switch_receipt=uuid4(), switched_by=uuid4(), switched_by_name="Earlier chooser")
        with patch("src.intevia.services.office_record.office_record", return_value={"office": {"organism_uuid": org}, "care": setting}):
            page = Markup(match.func(request, **match.kwargs).content.decode())
        cards = page.select("section", **{"aria-labelledby": "office-assistance"})
        self.assertEqual(len(cards), 1, "The office must show one assistance card for the delivered setting.")
        return page, cards[0]

    def test_state_pills_match_the_approved_card(self):
        for enabled, chosen, word, tone, source in (
            (True, False, "On", "ok", "On by default"),
            (False, True, "Off", "neutral", "Organisation choice"),
            (True, True, "On", "ok", "Organisation choice"),
        ):
            with self.subTest(word=word, source=source):
                page, card = self.read_card(enabled=enabled, chosen=chosen)
                pills = page.select("span", within=card, classes=("tag", tone))
                self.assertEqual([pill["text"] for pill in pills], [word],
                                 "On must have one green pill and Off one neutral pill.")
                self.assertEqual(pills[0]["parents"][-1]["text"], f"{word} · {source}",
                                 "The source of the setting must sit beside its state pill.")
        css = Path(finders.find("core/f0.css")).read_text()
        tag = declarations(css, ".tag")
        self.assertEqual((tag.get("display"), tag.get("align-items"), tag.get("padding"), tag.get("border-radius"),
                          tag.get("font-size"), tag.get("font-weight"), tag.get("font-family")),
                         ("inline-flex", "center", ".17rem .6rem", "2rem", ".78rem", "650", None),
                         "The state pill must keep the approved shape, weight and inherited body type.")
        for tone, role in (("ok", "success"), ("neutral", "pending")):
            style = declarations(css, ".tag." + tone)
            self.assertEqual((style.get("background"), style.get("color")),
                             (f"var(--color-{role}-bg)", f"var(--color-{role})"),
                             "The state pill must follow its approved colour roles in both modes.")

    def test_glass_pins_have_a_named_source(self):
        readme = Path(finders.find("core/brand/README.md")).read_text()
        reference = "REF_VIOLET_GLASS_CLASSIC_DEEPENED_v1_0.html"
        digest = "c583970b14c259b33e02bebb21b5f6f9f949ca8bdc87d0931d9f5759ee478eef"
        self.assertTrue(any(reference in line and digest in line for line in readme.splitlines()),
                        "The brand README must pair the named glass reference with its complete digest.")

    def test_dark_reference_selects_the_page_palette(self):
        source = theme.THEME.read_text()
        expected = theme.reference_bytes()
        decoy = "@variant dark-page { --color-text:#FFFFFF; }\n"
        with TemporaryDirectory() as directory:
            sample = Path(directory) / "theme.css"
            with patch.object(theme, "THEME", sample):
                sample.write_text(decoy + source)
                self.assertEqual(theme.reference_bytes(), expected,
                                 "An earlier dark component block must not replace the actual page palette.")
                before, variant, palette = source.partition("@variant dark-page")
                sample.write_text(before + variant + palette.replace("--color-page:", "--other-page:"))
                with self.assertRaisesMessage(ValueError, "The dark-page palette must declare --color-page."):
                    theme.reference_bytes()

    def test_cache_exports_appearance_privacy(self):
        self.assertIn("appearance_privacy", cache.__all__, "The cache module must export the appearance privacy middleware.")

    def test_provision_labels_cover_the_model(self):
        self.assertSetEqual(set(_PROVISION_LABELS), set(OrganismRoleDefinitionProvision.Relationship.values),
                            "Every model provision relationship must have one display label, with no surplus keys.")

    def test_coordinator_guidance_belongs_to_the_current_reader(self):
        wording = "As Organisation Coordinator, you can change this."
        for coordinator in (True, False):
            page, card = self.read_card(chosen=True, coordinator=coordinator)
            chooser = page.select("dl", within=card)[0]
            self.assertEqual([n["text"] for n in page.select("dd", within=chooser)], ["Earlier chooser"],
                             "The named chooser must remain the person who made the choice.")
            direct = [n for n in page.nodes if n["parents"] and n["parents"][-1] is card]
            guidance = [n for n in direct if n["tag"] == "p" and n["text"] == wording]
            if coordinator:
                position = next(i for i, node in enumerate(direct) if node is chooser)
                self.assertEqual([n["text"] for n in direct[position + 1:position + 2]], [wording],
                                 "The current Organisation Coordinator must see the exact guidance under the chooser.")
            else:
                self.assertFalse(guidance, "An ordinary reader must never be told that they can change the setting.")

    def test_card_offers_only_the_change_of_state(self):
        for enabled, chosen in ((True, False), (True, True), (False, True)):
            with self.subTest(enabled=enabled, chosen=chosen):
                page, card = self.read_card(enabled=enabled, chosen=chosen)
                forms = page.select("form", within=card, method="post")
                self.assertEqual([n["text"].strip() for f in forms for n in page.select("button", within=f)],
                                 ["Turn assistance off" if enabled else "Turn assistance on"],
                                 "The card must offer only the opposite setting, including at default On.")
                expected = "text-command" if enabled else "button command primary"
                self.assertEqual([n["attrs"]["class"] for f in forms for n in page.select("button", within=f)], [expected],
                                 "Turn assistance off must be a text command and Turn assistance on must remain primary.")
                self.assertEqual([n["attrs"]["value"] for f in forms for n in page.select("input", within=f, name="enabled")],
                                 ["off" if enabled else "on"], "The one card command must submit the indicated choice.")

    def test_off_is_a_post_submit_with_the_appearance_of_a_link(self):
        page, card = self.read_card()
        forms = page.select("form", within=card)
        self.assertEqual([form["attrs"].get("method") for form in forms], ["post"],
                         "Turn assistance off must use the existing POST form.")
        form = forms[0]
        self.assertEqual(resolve(form["attrs"]["action"]).url_name, "office_switch",
                         "The text command must post to the existing office switch.")
        self.assertTrue(page.select("input", within=form, name="csrfmiddlewaretoken"),
                        "The text command must retain its CSRF field.")
        submits = page.select("button", within=form, type="submit")
        self.assertEqual([(n["text"].strip(), n["attrs"]["class"]) for n in submits],
                         [("Turn assistance off", "text-command")],
                         "The Off action must be a native submit button carrying only the text-command class.")
        self.assertFalse(page.select("a", within=form), "The Off form must contain no navigation link.")
        css = Path(finders.find("core/f0.css")).read_text()
        style = declarations(css, ".text-command")
        self.assertEqual([style.get(k) for k in ("appearance", "background", "border", "box-shadow")],
                         ["none", "0 0", "0", "none"], "The text command must have no button chrome.")
        self.assertEqual((style.get("color"), style.get("text-decoration-line")),
                         ("var(--color-primary)", "underline"),
                         "The text command must use the mode's violet link colour and a visible underline.")
        from tests.test_uc1_render import contrast
        for selector in (":root,:host", "html[data-mode=dark]", "html:not([data-mode])"):
            with self.subTest(selector=selector):
                palette = declarations(css, selector)
                self.assertGreaterEqual(contrast(palette["--color-primary"], palette["--color-surface"]), 4.5,
                                        "The link label must remain legible on the card in each mode.")
