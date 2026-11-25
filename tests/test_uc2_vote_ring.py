"""Plan box 8d: the open vote's count-only ring (front-end foundation v0.6), and its arithmetic."""
from pathlib import Path
from uuid import uuid4

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from core.ui import count_ring, participation
from src.intevia.services import cq, cq_reads
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_ballot_reads import open_board_vote
from tests.test_uc1_render import contrast, declarations
from tests.test_uc2_pages import FIXTURE_HASHERS


def arcs(markup):
    """Each drawn arc of the first ring: its kind, and its share of the circle."""
    page = Markup(markup)
    return [(node["attrs"]["class"], node["attrs"]["stroke-dasharray"].split()[0])
            for node in page.select("circle", classes=("ring-segment",))]


class RingArithmeticTests(SimpleTestCase):
    """B8D-02. No database: the shares, the words and the shared component."""

    def test_b8d_02_the_ring_shares_follow_the_count(self):
        ring = count_ring(1, 3, "Board members")
        self.assertEqual(ring["text"], "1 of 3 Board members have answered",
                         "The ring must say its count in words for a screen reader.")
        self.assertEqual([(s["key"], s["length"], s["offset"], s["glass"]) for s in ring["segments"]],
                         [("answered", "33.333", "0.000", True), ("pending", "66.667", "-33.333", False)],
                         "One answer of three is a third of the circle, glassy; the rest follows it, flat.")
        self.assertEqual([(c["label"], c["count"]) for c in ring["counts"]], [("Answered", 1), ("Not yet", 2)],
                         "The words beside the ring must carry the same count without colour.")
        markup = render_to_string("core/components/participation_ring.html", {"ring": ring})
        self.assertEqual(arcs(markup), [("ring-segment answered glass", "33.333"), ("ring-segment pending", "66.667")],
                         "The drawn arcs must be the counted shares.")
        self.assertEqual(arcs(render_to_string("core/components/participation_ring.html",
                                               {"ring": count_ring(0, 3, "Board members")})),
                         [("ring-segment pending", "100.000")], "With no answer yet no answered arc is drawn.")
        self.assertEqual(count_ring(0, 0, "Board members")["segments"][0]["length"], "0.000",
                         "An empty electorate must not break the page.")
        shared = render_to_string("core/components/participation.html", {"vote": participation(
            counts={"consented": 42, "declined": 18, "abstained": 10, "pending": 50}, anonymous=True)})
        self.assertEqual([share for _, share in arcs(shared)], ["35.000", "15.000", "8.333", "41.667"],
                         "The shared participation component must draw the same ring from its four counts.")


DARK = ("html[data-mode=dark] ", "html:not([data-mode]) ")
PILLS = ":is(.tag.ok,.board-office-state:not(.vacant))"
REFUSALS = ":is(.notice.refusal,.notice.error)"


def layers(css, index):
    """The @layer blocks that enclose one position of the compiled stylesheet."""
    stack, start = [], 0
    for at, char in enumerate(css[:index]):
        if char == "{":
            stack.append(css[start:at].strip())
            start = at + 1
        elif char == "}":
            if stack:
                stack.pop()
            start = at + 1
        elif char == ";":
            start = at + 1
    return [prelude for prelude in stack if prelude.startswith("@layer")]


class DarkStateColourTests(SimpleTestCase):
    """B8D-03 and B8D-04 (Advisor box 8 part 2, findings 1 and 2), read from the served stylesheet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = Path(finders.find("core/f0.css")).read_text()

    def palette(self, prefix):
        return declarations(self.css, ":root,:host") | declarations(self.css, prefix.strip())

    def colour(self, value, palette):
        value = (palette[value[4:-1]] if value.startswith("var(") else value).lower()
        return "#" + "".join(char * 2 for char in value[1:]) if len(value) == 4 else value

    def test_b8d_03_dark_held_and_on_pills_are_white_on_the_for_green(self):
        self.assertEqual(declarations(self.css, ".tag.ok").get("color"), "var(--color-success)",
                         "The unlayered pill rule this override must beat sets the green words.")
        for prefix in DARK:
            with self.subTest(prefix=prefix):
                selector = prefix + PILLS
                sites = [at for at in range(len(self.css)) if self.css.startswith(selector + "{", at)]
                self.assertTrue(sites, "Dark mode must give the Held and On pills their own colours.")
                self.assertEqual([layers(self.css, at) for at in sites], [[]] * len(sites),
                                 "The dark pill override must sit outside any layer, or the unlayered pill rule wins.")
                rule, palette = declarations(self.css, selector), self.palette(prefix)
                ink, fill = self.colour(rule["color"], palette), self.colour(rule["background"], palette)
                self.assertEqual((ink, fill), ("#ffffff", "#186448"), "Dark Held and On read white on the For green.")
                self.assertGreaterEqual(contrast(ink, fill), 4.5, "The dark pills must reach 4.5:1.")

    def test_b8d_05_every_green_text_rule_turns_white_on_dark(self):
        """Captures J and M: on dark the green is a fill or an edge. Each rule that writes in the green has its override."""
        import re
        written = sorted({rule[2].strip() for rule in re.finditer(r"(^|})([^{}@]+)\{([^{}]*)\}", self.css)
                          if re.search(r"(^|;)color:var\(--color-success\)", rule[3])})
        self.assertEqual(written, [".answer.consented", ".board-office-state", ".notice.success", ".tag.ok"],
                         "A new rule that writes in the green needs its own white dark override here.")
        # Capture Q (D1): nor may any rule write a dark green as a literal colour.
        greens = {"#186448", "#1c7550", "#165c41", "#124d36", "#10432f", "#0f2a1c"}
        literal = sorted({rule[2].strip() for rule in re.finditer(r"(^|})([^{}@]+)\{([^{}]*)\}", self.css.lower())
                          if any(name.strip() == "color" and value.strip() in greens
                                 for name, _, value in (part.partition(":") for part in rule[3].split(";")))})
        self.assertEqual(literal, [], "No rule may write words in a dark green given as a literal colour.")
        overrides = {".answer.consented": ".answer.consented", ".notice.success": ".notice.success",
                     ".tag.ok": PILLS, ".board-office-state": PILLS}
        for prefix in DARK:
            for rule, selector in overrides.items():
                with self.subTest(prefix=prefix, rule=rule):
                    self.assertEqual(declarations(self.css, prefix + selector).get("color"), "var(--color-on-button)",
                                     "On dark this rule must write in white, not in the green.")

    def test_b8d_04_dark_refusal_words_keep_contrast_under_the_highlight(self):
        for prefix in DARK:
            with self.subTest(prefix=prefix):
                palette = self.palette(prefix)
                shine = declarations(self.css, prefix + REFUSALS).get("--glass-shine", palette["--glass-shine"])
                ground = palette["--color-danger-bg"].lstrip("#")
                alpha = int(shine[7:9], 16) / 255
                top = "#" + "".join(f"{round(int(ground[i:i + 2], 16) * (1 - alpha) + 255 * alpha):02x}" for i in (0, 2, 4))
                self.assertGreaterEqual(contrast(palette["--color-danger"], top), 4.5,
                                        "The red words must reach 4.5:1 where the highlight is strongest.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class VoteRingPageTests(TransactionTestCase):
    """B8D-01. The real Topic page while voting is open, under NAMED reporting."""

    def test_b8d_01_the_open_vote_shows_the_count_ring_and_no_name(self):
        case = open_board_vote()
        founder, coordinator, trustee = case.people
        ballot = cq_reads.vote_status(**case.scope)["ballot"]
        cq.answer(**(case.scope | {"credential": founder.credential}), ballot_uuid=ballot, choice="QUALIFIED",
                  request_uuid=uuid4())
        self.assertEqual(cq_reads.vote_status(**case.scope)["reporting_rule"]["value"], "NAMED",
                         "This witness needs the platform default, which reports by name.")
        page = Markup(signed_in(trustee).get(case.url).content.decode())
        section, = page.select("section", id="vote")
        rings = page.select("div", within=section, classes=("participation-ring",))
        self.assertEqual([(ring["attrs"].get("role"), ring["attrs"].get("aria-label")) for ring in rings],
                         [("img", "1 of 3 Board members have answered")],
                         "While voting is open the vote section must show one ring that states its count in words.")
        self.assertEqual([(node["attrs"]["class"], node["attrs"]["stroke-dasharray"].split()[0])
                          for node in page.select("circle", within=section, classes=("ring-segment",))],
                         [("ring-segment answered glass", "33.333"), ("ring-segment pending", "66.667")],
                         "The ring must draw one answered third, glassy, and the rest flat.")
        summary, = page.select("div", within=section, classes=("participation-summary",))
        self.assertEqual([(p["text"].split()[1:-1], p["text"].split()[-1]) for p in page.select("p", within=summary)],
                         [(["Answered"], "1"), (["Not", "yet"], "2")],
                         "Beside the ring the count must read in words: Answered 1, Not yet 2.")
        # Part PB-6d (capture AY3) adds the ruled line of how many more are needed (``#vote-progress``); the old plain count line stays gone.
        self.assertEqual([p["text"] for p in page.select("p", within=section)
                          if "have answered" in p["text"] and p["attrs"].get("id") != "vote-progress"], [],
                         "The plain count line must be gone: the ring replaces it.")
        self.assertEqual([p["text"] for p in page.select("p", within=section, id="vote-progress")],
                         ["So far 1 has answered. 2 more are needed for the vote to count."],
                         "A Board of three needs three to take part; one has answered.")
        names = [person.display_name for person in case.people]
        self.assertTrue(all(names), "Each fixture trustee needs a display name for this witness to mean anything.")
        self.assertEqual([name for name in names if name in section["text"] or name in rings[0]["attrs"]["aria-label"]],
                         [], "The ring must not name who has or has not answered.")
        status, = [p["text"].strip() for p in page.select("p", classes=("meta",)) if p["text"].strip().startswith("Suggestion")]
        self.assertTrue(status.endswith(" · Voting open · 1 of 3 answered"),
                        "The status under the Topic's title stays as built.")
