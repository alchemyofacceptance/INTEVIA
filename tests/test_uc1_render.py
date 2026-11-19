"""Server-side visual witnesses for the shared U-C1 shell and its served stylesheet."""
import hashlib
import re
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve, reverse

from tests.test_f0_ui import Markup


def declarations(stylesheet, selector):
    """Read the declaration blocks for one selector in the pinned compiler's output."""
    result = {}
    for match in re.finditer(r"(?:^|[{}])" + re.escape(selector) + r"\{([^{}]*)\}", stylesheet):
        for declaration in match[1].split(";"):
            name, sep, value = declaration.partition(":")
            if sep:
                result[name.strip()] = value.strip()
    return result


def ink_value(value, palette):
    role = re.fullmatch(r"var\((--[\w-]+)\)", value)
    return palette[role[1]] if role else value


def luminance(colour):
    # The compiler also uses CSS named colours and short hex forms.
    hex_value = {"indigo": "#4b0082", "white": "#ffffff"}.get(colour, colour).removeprefix("#")
    if len(hex_value) == 3:
        hex_value = "".join(char * 2 for char in hex_value)
    channels = [int(hex_value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))


def contrast(first, second):
    lower, higher = sorted((luminance(first), luminance(second)))
    return (higher + 0.05) / (lower + 0.05)


class RenderedScreenTests(SimpleTestCase):
    def test_header_uses_the_named_tree(self):
        """The shared header delivers both decorative trees, their intrinsic sizes and one wordmark."""
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200, "A visitor must receive the shared sign-in shell.")
        page = Markup(response.content.decode())
        brands = page.select("a", classes=("brand",))
        self.assertEqual(len(brands), 1, "The header must have one brand link.")
        brand = brands[0]
        self.assertEqual(brand["text"].strip(), "INTEVIA", "The brand must speak the wordmark once.")
        self.assertEqual(len(page.select("img", within=brand)), 2, "The brand must emit exactly two tree images.")
        for mode, filename, size in (("light", "tree.png", (160, 217)), ("dark", "tree-dark-title.png", (821, 1120))):
            with self.subTest(mode=mode):
                images = page.select("img", within=brand, classes=(f"{mode}-tree",))
                self.assertEqual(len(images), 1, f"The brand must emit one {mode} tree with its mode class.")
                attrs = images[0]["attrs"]
                asset = f"core/brand/{filename}"
                self.assertEqual(attrs.get("src"), static(asset), f"The {mode} header must use its named tree file.")
                self.assertEqual(attrs.get("alt"), "", "Each tree must defer its accessible name to the wordmark.")
                self.assertEqual((attrs.get("width"), attrs.get("height")), tuple(map(str, size)),
                                 f"The {mode} tree must advertise its own intrinsic size.")
                found = finders.find(asset)
                self.assertTrue(found, f"The {mode} header image must resolve to a local static asset.")
                blob = Path(found).read_bytes()
                if mode == "dark":
                    self.assertEqual(hashlib.sha256(blob).hexdigest(), "6397e56904294a4d191c294562012465b2c34589962ed925ab4d3902199c1a25",
                                     "The dark header must use the title-slide tree.")
                self.assertEqual(struct.unpack(">II", blob[16:24]), size,
                                 f"The {mode} image dimensions must match its PNG header.")

        css = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        sizing = declarations(css, ".brand img")
        self.assertEqual(sizing.get("height"), "var(--logo-height)", "The trees must share the shell's logo height.")
        self.assertEqual(declarations(css, ":root").get("--logo-height"), "2.7rem",
                         "The header mark must be 2.7rem high.")
        self.assertEqual(sizing.get("width"), "auto", "Each tree must retain its own aspect ratio.")
        forced = declarations(css, "html[data-mode=dark]")
        for mode, fallback, dark_display in (("light", "block", "none"), ("dark", "none", "block")):
            with self.subTest(tree=mode):
                prop = f"--{mode}-tree-display"
                self.assertEqual(declarations(css, f".{mode}-tree").get("display"), f"var({prop},{fallback})",
                                 f"The {mode} tree must follow the shell's CSS display choice.")
                self.assertEqual(forced.get(prop), dark_display,
                                 f"The dark palette must set the {mode} tree to {dark_display}.")

    def test_every_used_pair_has_contrast(self):
        """Page C's text, status, flat action, selection, boundary and focus pairs in the served CSS."""
        css = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        base = declarations(css, ":root,:host")
        forced = declarations(css, "html[data-mode=dark]")
        self.assertEqual(forced.get("color-scheme"), "dark", "A saved Dark preference must select dark colours.")
        media = re.search(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*html:not\(\[data-mode\]\)\s*\{([^{}]+)\}", css)
        self.assertIsNotNone(media, "A dark device must select dark colours only on an unforced root.")
        self.assertEqual(declarations("html{" + media[1] + "}", "html"), forced,
                         "Forced dark and device dark must use the same palette and tree.")
        link_ink = declarations(css, "a").get("color", "")
        self.assertTrue(link_ink, "The served stylesheet must specify link ink.")
        dark = base | forced
        link_colour = ink_value(link_ink, dark)
        self.assertGreaterEqual(contrast(link_colour, dark["--color-surface"]), 4.5,
                                "The small dark-surface link must reach 4.5 to one.")
        grounds = ("page", "surface", "band")
        soft = ("primary-soft", "pending-bg", "accent-soft", "success-bg", "warning-bg", "danger-bg")
        text_pairs = [(ink, ground) for ink in ("text", "heading", "muted", "primary", "accent", "danger")
                      for ground in grounds]
        text_pairs += [("primary", "primary-soft"), ("pending", "pending-bg"), ("accent", "accent-soft"),
                       ("warning", "warning-bg"), ("danger", "danger-bg"), ("success", "success-bg"),
                       ("surface", "success"), ("surface", "danger"), ("on-button", "button")]
        edges = [(ink, ground) for ink in ("focus", "control") for ground in grounds + soft]
        for mode, palette in (("light", base), ("dark", dark)):
            # The primary control has its own reference witness. Its flat fallback remains in the token inventory.
            button = declarations(css, ".button,button.command")
            for variant in ("secondary", "success", "danger", "disabled"):
                with self.subTest(mode=mode, button=variant):
                    if variant == "disabled":
                        button_style = button | declarations(css, ".button:disabled,.button.primary[aria-disabled=true]")
                    else:
                        button_style = button | declarations(css, f".button.{variant}")
                    if variant in ("success", "danger"):
                        button_style.update(declarations(css, ".button.success,.button.danger"))
                    ink = ink_value(button_style["color"], palette)
                    ground = ink_value(button_style["background"], palette)
                    self.assertGreaterEqual(contrast(ink, ground), 4.5,
                                            f"The {mode} {variant} button label must reach 4.5 to one.")
            boundary = "button" if mode == "light" else "violet"
            edge_pairs = edges + [(boundary, "page"), (boundary, "surface")]
            for threshold, pairs in ((4.5, text_pairs), (3.0, edge_pairs)):
                for ink, ground in pairs:
                    with self.subTest(mode=mode, ink=ink, ground=ground):
                        self.assertGreaterEqual(contrast(palette[f"--color-{ink}"], palette[f"--color-{ground}"]),
                                                threshold, f"{mode} {ink} on {ground} must reach {threshold} to one.")

    def test_primary_commands_use_the_approved_glass(self):
        """Live primary commands and links use the approved pair in both modes; inactive controls stay flat."""
        def control(**values):
            markup = render_to_string("core/components/button.html", {"label": "Continue", **values})
            return Markup(markup).nodes[0]["attrs"]

        for variant in ("primary", "secondary", "success", "danger"):
            with self.subTest(variant=variant):
                button = control(variant=variant)
                self.assertEqual("command" in button["class"].split(), variant == "primary",
                                 "Only a primary button must opt into the command treatment.")
                self.assertIn(variant, button["class"].split(), "A button must retain its requested variant.")
        for href in ("", "/you/"):
            for state in ("enabled", "disabled", "loading"):
                with self.subTest(href=href, state=state):
                    attrs = control(href=href, disabled=state == "disabled", loading=state == "loading")
                    self.assertIn("primary", attrs["class"].split(), "The default action must keep its primary class.")
                    self.assertEqual(attrs.get("aria-busy") == "true", state == "loading",
                                     "Only a working action must report that it is busy.")
                    if href:
                        self.assertEqual(attrs.get("href"), href if state == "enabled" else None,
                                         "Only an enabled primary link may offer a destination.")
                        self.assertEqual(attrs.get("aria-disabled") == "true", state != "enabled",
                                         "An unavailable primary link must expose its disabled state.")
                    else:
                        self.assertEqual("disabled" in attrs, state != "enabled",
                                         "An unavailable primary button must be natively disabled.")

        # Pins cover the background and box-shadow, compiled independently from .b-l and .b-dd.
        # Pinned Tailwind intentionally normalises colour literals and property syntax.
        css = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        live = ":is(button.command.primary,a.button.primary):not(:disabled,[aria-disabled=true],[aria-busy=true])"
        light_pin = "f7fe18120c1361a43ede85a30ed0605f313d6e21d99c452e03bc254366c92350"
        dark_pin = "22f3203fc8547e722168901987409c27d14b8a0d347c6c983ff7343d94cedb23"
        for prefix, pin, mode in (("", light_pin, "light"), ("html[data-mode=dark] ", dark_pin, "forced dark"),
                                  ("html:not([data-mode]) ", dark_pin, "device dark")):
            with self.subTest(mode=mode):
                rule = declarations(css, prefix + live)
                paint = f"background:{rule.get('background', '')};box-shadow:{rule.get('box-shadow', '')}"
                self.assertEqual(hashlib.sha256(paint.encode()).hexdigest(), pin,
                                 f"The {mode} primary action must use the approved reference CSS.")
        self.assertEqual(declarations(css, live).get("color"), "var(--color-on-button)",
                         "The live primary label must retain its own ink inside a coloured notice.")
        self.assertIn("@media (prefers-color-scheme:dark){html:not([data-mode]) " + live, css,
                      "Device-dark primary paint must apply only on a dark device without a saved mode.")

    def test_notice_and_heading_share_the_page_column(self):
        """Shell, You and Events share one inset for the painted band and heading in both modes."""
        home = SimpleNamespace(display_name="Avery", events=[])
        user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: home.display_name)
        factory = RequestFactory()
        with patch("core.views.EventReadService.home", return_value=home):
            for route in ("shell", "f0-you", "event-list"):
                for mode in ("light", "dark"):
                    with self.subTest(route=route, mode=mode):
                        request = factory.get(reverse(route))
                        request.user = user
                        request.intevia_identity = SimpleNamespace(display_name=home.display_name, access_state="active",
                                                                  colour_mode=mode)
                        request.resolver_match = resolve(request.path_info)
                        response = request.resolver_match.func(request)
                        self.assertEqual(response.status_code, 200, "The requested page must render successfully.")
                        page = Markup(response.content.decode())
                        self.assertTrue(page.select("html", **{"data-mode": mode}), "The page must render in its test mode.")
                        columns = page.select("div", classes=("page-column",))
                        self.assertEqual(len(columns), 1, "The page must have one shared content column.")
                        column = columns[0]
                        frame = column["parents"][-1]
                        siblings = [node for node in page.nodes if node["parents"] and
                                    node["parents"][-1] is frame["parents"][-1]]
                        navs = page.select("nav", classes=("main-nav",))
                        self.assertTrue(len(navs) == 1 and any(left is navs[0] and right is frame
                                                               for left, right in zip(siblings, siblings[1:]))
                                        and frame["tag"] == "div" and "page-frame" in frame["attrs"]["class"].split(),
                                        "The main navigation must immediately precede the page frame as its sibling.")
                        bands = page.select("aside", classes=("capability-notice",))
                        self.assertEqual(len(bands), 1, "The signed-in page must show one stage notice.")
                        self.assertIs(bands[0]["parents"][-1], column,
                                      "The painted notice must sit directly inside the page column.")
                        mains = page.select("main", id="main-content")
                        self.assertEqual(len(mains), 1, "The skip link must retain one main-content destination.")
                        self.assertIs(mains[0]["parents"][-1], column, "Main must share the stage notice's column.")
                        headings = page.select("h1", within=mains[0])
                        self.assertEqual(len(headings), 1, "The requested page must have one heading.")
                        self.assertIs(headings[0]["parents"][-1], mains[0],
                                      "The page heading must start at the main content edge.")
                        self.assertFalse(page.select("nav", within=column), "Frozen navigation must stay outside the column.")

        css = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        column_style = declarations(css, ".page-column")
        self.assertEqual(column_style.get("padding-inline"), "var(--space-md)",
                         "The shared column must supply the horizontal content gutter.")
        self.assertEqual(column_style.get("margin-inline"), "auto", "The bounded column must centre in its frame.")
        self.assertEqual(column_style.get("max-width"), "var(--page-width)", "The shared column must keep the page width.")
        main = declarations(css, "main")
        insets = {"padding", "padding-left", "padding-inline", "padding-inline-start", "margin", "margin-left",
                  "margin-inline", "margin-inline-start"}
        self.assertFalse(insets.intersection(main), "Main must add no second horizontal inset after the shared gutter.")
        # Check each h1 declaration block, including responsive rules. Vertical spacing may be nonzero.
        inline_properties = insets | {"margin-right", "padding-right", "margin-inline-end", "padding-inline-end", "text-indent"}
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if not any(re.search(r"(?:^|[ >+~])h1$", selector.strip()) for selector in selectors.split(",")):
                continue
            for declaration in body.split(";"):
                prop, _, value = declaration.partition(":")
                if prop not in inline_properties:
                    continue
                values = value.split()
                if prop in {"padding", "margin"} and len(values) > 1:
                    values = values[1::2]  # right and left in a CSS shorthand
                self.assertTrue(all(re.fullmatch(r"0(?:px|rem|em|%)?", v) for v in values),
                                "The heading text must have zero inline inset at every width.")
        band = declarations(css, ".capability-notice")
        self.assertEqual(band.get("background"), "var(--color-warning-bg)", "The stage notice must paint its own rectangle.")
        self.assertFalse(any(k.startswith("margin") for k in band), "The band must have no independent outer margin.")
        desktop = css.partition("@media (min-width:64rem){")[2].partition("@media ")[0]
        self.assertEqual(declarations(desktop, ".main-nav+.page-frame").get("padding-inline-start"),
                         "calc(var(--rail) + var(--space-lg))", "Desktop rail clearance must belong to the outer frame.")

    def test_state_words_survive_both_colour_modes(self):
        """The selected appearance, current tab and unavailable domains retain their names."""
        factory = RequestFactory()
        expected = {"device": "Use device setting", "light": "Light", "dark": "Dark"}
        for mode in ("light", "dark"):
            with self.subTest(mode=mode):
                request = factory.get(reverse("f0-you"))
                request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Avery")
                request.intevia_identity = SimpleNamespace(display_name="Avery", access_state="active", colour_mode=mode)
                request.resolver_match = resolve(request.path_info)
                page = Markup(request.resolver_match.func(request).content.decode())
                inputs = page.select("input", name="colour_mode")
                labels = {node["attrs"]["value"]: node["parents"][-1]["text"].strip() for node in inputs
                          if node["parents"][-1]["tag"] == "label"}
                self.assertEqual(labels, expected, "Every appearance choice must have its plain-language label.")
                selected = [node["attrs"]["value"] for node in inputs if "checked" in node["attrs"]]
                self.assertEqual(selected, [mode], "The checked control must identify the saved choice without colour.")
                tabs = page.select("a", classes=("nav-item",), **{"aria-current": "page"})
                self.assertEqual([n["text"].strip() for n in tabs], ["You"],
                                 "The current You tab must keep its name and current-page state.")
                domains = page.select("a", classes=("domain-item",))
                expected_domains = ["Library", "Education", "Events", "Service", "Discussion"]
                self.assertCountEqual([" ".join(n["text"].split()) for n in domains],
                                      [name + " — arrives later" for name in expected_domains],
                                      "Every unavailable You domain must say in words that it arrives later.")
