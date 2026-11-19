"""Derive the catalogue's token values and contrast ratios from F0's single theme."""
import json
import re
from pathlib import Path

THEME = Path(__file__).resolve().parents[1] / "core/styles/theme.css"
DECLARATION = re.compile(r"--([\w-]+):([^;{}]+);")


def luminance(colour):
    channels = [int(colour[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def ratio(palette, ink, ground):
    low, high = sorted(luminance(palette[name]) for name in (ink, ground))
    return round((high + 0.05) / (low + 0.05), 2)


def contrast_pairs():
    for ground in ("page", "surface", "band"):
        for ink in ("text", "heading", "muted", "primary", "accent", "danger"):
            yield ink, ground, "Text"
    for ink, ground in (("primary", "primary-soft"), ("accent", "accent-soft"),
                        ("success", "success-bg"), ("warning", "warning-bg"),
                        ("danger", "danger-bg"), ("pending", "pending-bg")):
        yield ink, ground, "Text and status symbol"
    for ink, ground in (("on-button", "button"), ("surface", "success"), ("surface", "danger")):
        yield ink, ground, "Flat fallback label" if ink == "on-button" else "Flat button label"
    for ground in ("surface", "page", "band", "primary-soft", "pending-bg", "accent-soft",
                   "success-bg", "warning-bg", "danger-bg"):
        yield "control", ground, "Control boundary"
        yield "focus", ground, "Focus outline"
    for ink in ("violet", "luminous", "moon"):
        yield ink, "night", "Brand palette"
    yield "line", "surface", "Decorative divider"


def reference_bytes():
    source = THEME.read_text(encoding="utf-8")
    light = dict(DECLARATION.findall(source.partition("@theme {")[2].partition("@layer base")[0]))
    for block in re.findall(r"@variant\s+dark-page\s*\{([^{}]+)\}", source):
        overrides = dict(DECLARATION.findall(block))
        if "color-page" in overrides:
            break
    else:
        raise ValueError("The dark-page palette must declare --color-page.")
    dark = light | overrides
    tokens = [{"name": name, "value": value.strip(), "dark_value": dark[name].strip()}
              for name, value in light.items()]
    palettes = [{name.removeprefix("color-"): value for name, value in mode.items()
                 if name.startswith("color-")} for mode in (light, dark)]
    contrast = [{"pair": f"{ink} / {ground}", "ratio": ratio(palettes[0], ink, ground),
                 "dark_ratio": ratio(palettes[1], ink, ground), "use": use}
                for ink, ground, use in contrast_pairs()]
    # The light control is bounded by its fill. The dark control has a separate rim.
    for ground in ("page", "surface"):
        contrast.append({"pair": f"Flat fallback edge / {ground}", "ratio": ratio(palettes[0], "button", ground),
                         "dark_ratio": ratio(palettes[1], "violet", ground), "use": "Control boundary"})
    return (json.dumps({"tokens": tokens, "contrast": contrast}, indent=2) + "\n").encode("utf-8")
