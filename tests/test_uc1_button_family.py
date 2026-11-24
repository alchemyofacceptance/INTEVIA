"""Compare the served button family with the frozen, individually viewable reference."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from tests.test_f0_ui import Markup
from tests.test_uc1_render import declarations, ink_value


REFERENCE = "REF_U_C1_GLASS_BUTTON_FAMILY_v1_2.html"
LIVE = ":not(:disabled,[aria-disabled=true],[aria-busy=true])"
FAMILY = ".button:is(.secondary,.success,.danger)" + LIVE
VARIANTS = ("primary", "secondary", "success", "danger")


class GlassButtonTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = Path(finders.find("core/f0.css")).read_text()
        cls.reference = Path(finders.find("core/brand/" + REFERENCE)).read_bytes()
        cls.panes = [Markup(frame["attrs"]["srcdoc"]) for frame in Markup(cls.reference.decode()).select("iframe")]
        cls.frozen = cls.panes[0].select("style", id="served-theme")[0]["text"]

    def test_secondary_glass_keeps_the_surface_and_violet_outline(self):
        edge = declarations(self.css, ".button.secondary" + LIVE)
        self.assertEqual((edge.get("color"), edge.get("border-color")), ("var(--color-primary)",) * 2,
                         "The active secondary label and outline must both use the mode's violet.")
        for prefix in ("", "html[data-mode=dark] ", "html:not([data-mode]) "):
            face = prefix + ".button.secondary"
            self.assertEqual(declarations(self.css, face), declarations(self.frozen, face),
                             "The secondary face must retain the reference surface in each appearance mode.")
        self.assertEqual(declarations(self.css, FAMILY), declarations(self.frozen, FAMILY),
                         "The secondary must use the reference highlight band, bright edge and inner shadow.")

    def test_success_and_danger_use_their_own_glass_faces(self):
        paint = declarations(self.css, FAMILY)
        self.assertEqual(paint, declarations(self.frozen, FAMILY),
                         "The active green and red buttons must share the reference glass paint.")
        for variant in ("success", "danger"):
            for prefix in ("", "html[data-mode=dark] ", "html:not([data-mode]) "):
                with self.subTest(variant=variant, prefix=prefix):
                    selector = prefix + ".button." + variant
                    if variant == "danger" and prefix:
                        face = declarations(self.css, selector)
                        live = declarations(self.css, selector + LIVE)
                        palette = declarations(self.css, ":root,:host") | declarations(self.css, prefix.strip())
                        self.assertEqual(
                            (face.get("--glass-face"), face.get("--glass-foot"),
                             ink_value(live.get("color", ""), palette)), ("#b0182c", "#8f1b28", "#fff"),
                            "Dark danger must have the two saturated red stops and a white label.")
                        self.assertEqual(live, declarations(self.frozen, selector + LIVE),
                                         "Dark danger must keep its reference band, bevel, rose rim and red glow.")
                    self.assertEqual(declarations(self.css, selector), declarations(self.frozen, selector),
                                     "The active coloured face must keep its own reference stops in each mode.")

    @override_settings(INTEVIA_DESIGN_CATALOGUE=True)
    def test_catalogue_and_pinned_reference_show_the_same_controls(self):
        readme = Path(finders.find("core/brand/README.md")).read_text()
        digest = hashlib.sha256(self.reference).hexdigest()
        self.assertTrue(any(REFERENCE in line and digest in line for line in readme.splitlines()),
                        "The brand README must identify the exact rendered button family reference.")
        self.assertEqual([p.select("html")[0]["attrs"].get("data-mode") for p in self.panes], ["light", "dark"],
                         "The reference must show one light pane and one dark pane.")
        for mode, pane in zip(("light","dark"), self.panes, strict=True):
            request = RequestFactory().get(reverse("f0-catalogue"))
            request.user = AnonymousUser()
            request.intevia_identity = SimpleNamespace(colour_mode=mode)
            request.resolver_match = match = resolve(request.path)
            catalogue = Markup(match.func(request).content.decode())
            heading = next(node for node in catalogue.select("h2") if node["text"] == "Buttons")
            section = heading["parents"][-1]
            for sample in (pane, catalogue):
                commands = sample.select("button", classes=("text-command",), type="submit")
                self.assertEqual([n["text"].strip() for n in commands], ["Turn assistance off"],
                                 "Both views must show the link-styled Off submit alongside the glass family.")
            for variant in VARIANTS:
                with self.subTest(mode=mode, variant=variant):
                    def examples(page, within=None):
                        return [(n["text"].strip(), n["attrs"]) for n in page.select("button", within=within, classes=(variant,))
                                if n["attrs"].get("aria-busy") != "true"]
                    pair = examples(pane)
                    self.assertEqual([(text, "disabled" in attrs) for text, attrs in pair],
                                     [("Continue", False), ("Unavailable", True)],
                                     "Every reference variant must show both a live and a native disabled example.")
                    self.assertEqual(examples(catalogue, section), pair,
                                     "The catalogue must use the same normal and disabled controls as the reference.")

    def test_glass_label_contrast_includes_the_highlight_band(self):
        for key, minimum in contrast_samples(self.css).items():
            with self.subTest(case=key):
                self.assertGreaterEqual(minimum, 4.5, "The label must reach 4.5:1 over the composited glass band.")


def contrast_samples(css):
    """Sample the served gradients inside natural and wrapped content boxes, as for the violet reference."""
    import re
    from tests.test_uc1_render import contrast

    def rgb(value):
        value = {"white": "#ffffff", "indigo": "#4b0082"}.get(value, value)
        code = value.removeprefix("#")
        if len(code) in (3, 4):
            code = "".join(c * 2 for c in code)
        return tuple(int(code[i:i + 2], 16) for i in range(0, len(code), 2))

    def mix(a, b, t):
        return tuple(x + (y - x) * t for x, y in zip(a, b, strict=True))

    def hex_colour(rgb_values):
        return "#" + "".join(f"{round(v):02x}" for v in rgb_values)

    results = {}
    primary = ":is(button.command.primary,a.button.primary)" + LIVE
    modes = {"light": "", "dark": "html[data-mode=dark] ", "device-dark": "html:not([data-mode]) "}
    for mode, prefix in modes.items():
        palette = declarations(css, ":root,:host") | declarations(css, prefix.strip())
        def resolve_colour(value):
            if value.startswith("var("):
                return rgb(palette[value[4:-1]])
            match = re.fullmatch(r"color-mix\(in srgb,(var\([^)]+\)) (\d+)%,(var\([^)]+\))\)", value)
            if match:
                return mix(resolve_colour(match[3]), resolve_colour(match[1]), int(match[2]) / 100)
            return rgb(value)

        for variant in VARIANTS:
            sel = ".button." + variant
            face = declarations(css, sel) | declarations(css, prefix + sel)
            if variant == "primary":
                paint = declarations(css, prefix + primary)["background"]
                a, b = re.search(r",linear-gradient\((#[a-f\d]+),(#[a-f\d]+)\)$", paint).groups()
                ink = resolve_colour(declarations(css, primary)["color"])
            else:
                paint = declarations(css, FAMILY)["background"]
                a, b = face["--glass-face"], face["--glass-foot"]
                label = (declarations(css, sel + LIVE) if variant == "secondary"
                         else declarations(css, sel))
                override = declarations(css, prefix + sel + LIVE)
                paint = override.get("background", paint)
                ink = resolve_colour(override.get("color", label["color"]))
            top, mid, end = [rgb(v)[3] / 255 for v in re.findall(r"(#[a-f\d]+) (?:0|48|50)%", paint)]
            upper, lower = resolve_colour(a), resolve_colour(b)
            for lines, padding_fraction in (("natural", (42 - 24.8) / 84), ("wrapped", 8 / (2 * 24.8 + 16))):
                values = []
                for i in range(1001):
                    y = padding_fraction + (1 - 2 * padding_fraction) * i / 1000
                    alpha = (top + (mid - top) * y / .48 if y <= .48
                             else mid + (end - mid) * min(1, (y - .48) / .02))
                    background = mix(mix(upper, lower, y), (255, 255, 255), alpha)
                    values.append(contrast(hex_colour(ink), hex_colour(background)))
                results[mode, variant, lines] = min(values)
    return results
