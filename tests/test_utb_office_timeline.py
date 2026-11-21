"""Reader-visible office history, GET navigation and the lifetime of the page choice."""
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.template import Context, Template
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from psycopg import sql

from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import office_path, signed_in


def history(total):
    start = datetime(2026, 11, 1, tzinfo=UTC)
    return [
        {"kind": "law_adoption", "label": f"Adoption {n}", "at": start + timedelta(minutes=n)}
        for n in range(total, 0, -1)
    ]


def render(entries, **query):
    template = Template("{% load f0_ui %}{% office_timeline record query %}")
    return Markup(template.render(Context({"record": {"history": entries}, "query": query})))


def numbers_and_actions(page):
    result = []
    for item in page.select("li"):
        number, = page.select("span", within=item, classes=("office-entry-number",))
        first = next(n for n in page.nodes if n["parents"] and n["parents"][-1] is item)
        if first is not number:
            raise AssertionError("The entry number must be the first element in each entry.")
        action, = page.select("strong", within=item)
        result.append((number["text"], action["text"]))
    return result


class HistoryPagingTests(SimpleTestCase):
    def test_section_introduces_the_unboxed_history(self):
        page = render(history(2))
        section, = page.select("section", **{"aria-labelledby": "office-history"})
        self.assertNotIn("card", section["attrs"]["class"].split(), "Office history must stand outside a card.")
        self.assertIn("office-path", section["attrs"]["class"].split())
        children = [n for n in page.nodes if n["parents"] and n["parents"][-1] is section]
        heading = children[0]
        self.assertFalse(page.select("p", within=section, classes=("eyebrow",)))
        self.assertEqual((heading["tag"], heading["attrs"]["id"], heading["text"]),
                         ("h2", "office-history", "The path here"))

    def test_entry_numbers_survive_a_newer_visible_entry(self):
        entries = history(3)
        # This reader sees no CARE observation. Its presence in stale input must not affect N.
        withheld = {"kind": "care_observation", "at": entries[0]["at"]}
        before = numbers_and_actions(render([withheld, *entries[1:]]))
        after = numbers_and_actions(render([entries[0], withheld, *entries[1:]]))
        self.assertEqual([n.split(" of ")[0] for n, _ in before],
                         [n.split(" of ")[0] for n, _ in after[1:]],
                         "A newer visible entry must preserve each earlier entry's number.")
        self.assertEqual(before, [("Entry 2 of 2", "Adoption 2"), ("Entry 1 of 2", "Adoption 1")])
        self.assertEqual(after, [("Entry 3 of 3", "Adoption 3"), ("Entry 2 of 3", "Adoption 2"),
                                 ("Entry 1 of 3", "Adoption 1")])

    def test_page_size_and_same_page_get_form(self):
        single = render(history(1))
        summary, = single.select("div", classes=("office-history-bar",))
        self.assertEqual(single.select("span", within=summary)[0]["text"], "Showing 1 entry, newest first")
        entries = history(47)
        for query, size in (({}, 10), ({"show": "20"}, 20), ({"show": "11"}, 10),
                            ({"show": "50"}, 50), ({"show": "100"}, 100), ({"show": "200"}, 200)):
            with self.subTest(query=query):
                page = render(entries, **query)
                form, = page.select("form", method="get")
                self.assertEqual(urlsplit(form["attrs"].get("action", "")).path, "")
                label, = page.select("label", within=form)
                self.assertTrue(label["text"].strip().startswith("Show ") and label["text"].strip().endswith("at a time"))
                select, = page.select("select", within=label, name="show")
                options = page.select("option", within=select)
                self.assertEqual([(o["attrs"]["value"], o["text"]) for o in options],
                                 [(s, s) for s in ("10", "20", "50", "100", "200")])
                self.assertEqual([o["text"] for o in options if "selected" in o["attrs"]], [str(size)])
                submit, = page.select("button", within=form, type="submit")
                self.assertEqual(submit["text"], "Apply")
                self.assertNotIn("form", submit["attrs"], "Apply must belong to its enclosing native GET form.")
                self.assertNotIn("hidden", submit["attrs"])
                self.assertFalse(page.select("input", within=form), "Changing size starts on page one.")
                count = min(size, 47)
                self.assertEqual(numbers_and_actions(page),
                                 [(f"Entry {n} of 47", f"Adoption {n}") for n in range(47, 47 - count, -1)])
                bar, = page.select("div", classes=("office-history-bar",))
                summary, = page.select("span", within=bar)
                expected = ("Showing all 47 entries, newest first" if count == 47
                            else f"Showing entries 47 to {48 - count} of 47, newest first")
                self.assertEqual(summary["text"], expected)
                pager = page.select("nav", **{"aria-label": "Office history pages"})
                self.assertEqual(len(pager), int(count < 47))

    def test_pager_endpoints_and_page_bounds(self):
        entries = history(47)
        for requested, current, newest, oldest in (
            ("1", 1, 47, 28), ("0", 1, 47, 28), ("-2", 1, 47, 28), ("bad", 1, 47, 28),
            ("2", 2, 27, 8), ("+002", 2, 27, 8), ("3", 3, 7, 1), ("9999", 3, 7, 1),
            ("9" * 5000, 3, 7, 1), ("\u0663", 1, 47, 28),
        ):
            with self.subTest(requested=requested):
                page = render(entries, show="20", page=requested)
                self.assertEqual(numbers_and_actions(page),
                                 [(f"Entry {n} of 47", f"Adoption {n}") for n in range(newest, oldest - 1, -1)])
                bar, = page.select("div", classes=("office-history-bar",))
                self.assertEqual(page.select("span", within=bar)[0]["text"],
                                 f"Showing entries {newest} to {oldest} of 47, newest first")
                nav, = page.select("nav", **{"aria-label": "Office history pages"})
                newer, position, older = [n for n in page.nodes if n["parents"] and n["parents"][-1] is nav]
                self.assertEqual((position["tag"], position["text"]), ("span", f"Page {current} of 3"))
                for node, text, dest in ((newer, "‹ Newer", current - 1), (older, "Older ›", current + 1)):
                    self.assertEqual(node["text"], text)
                    if 1 <= dest <= 3:
                        self.assertEqual(node["tag"], "a")
                        target = urlsplit(node["attrs"]["href"])
                        self.assertEqual((target.netloc, target.path, parse_qs(target.query)),
                                         ("", "", {"show": ["20"], "page": [str(dest)]}))
                    else:
                        self.assertEqual(node["tag"], "span")
                        self.assertNotIn("href", node["attrs"])


@override_settings(ALLOWED_HOSTS=["testserver"])
class HistoryRequestTests(TransactionTestCase):
    def test_paging_choice_ends_with_the_request(self):
        from tests.office_record_support import approved_office
        from tests.test_law_acts import add_member

        owner, _, office, *_ = approved_office()
        client = signed_in(add_member(owner))
        path = office_path(owner, office)
        self.assertEqual(client.get(path).status_code, 200)  # Complete ordinary first display first.
        session_before = dict(client.session.items())
        cookies_before = {k: v.value for k, v in client.cookies.items()}
        # Exact cardinalities include tables written by database functions as well as Django models.
        tables = sorted(connection.introspection.table_names())
        counts = sql.SQL("SELECT ") + sql.SQL(", ").join(
            sql.SQL("(SELECT count(*) FROM {})").format(sql.Identifier(name)) for name in tables
        )
        with connection.cursor() as cursor:
            cursor.execute(counts)
            rows_before = dict(zip(tables, cursor.fetchone(), strict=True))
        for query, expected in (({"show": "20", "page": "2"}, "20"), ({}, "10")):
            with self.subTest(query=query):
                response = client.get(path, query)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(dict(client.session.items()) == session_before,
                                "Paging must leave the existing session and every key unchanged.")
                self.assertTrue({k: v.value for k, v in client.cookies.items()} == cookies_before,
                                "Paging must leave the browser's cookie names and values unchanged.")
                page = Markup(response.content.decode())
                select, = page.select("select", name="show")
                self.assertEqual([o["text"] for o in page.select("option", within=select)
                                  if "selected" in o["attrs"]], [expected])
                with connection.cursor() as cursor:
                    cursor.execute(counts)
                    self.assertEqual(dict(zip(tables, cursor.fetchone(), strict=True)), rows_before,
                                     "Paging must leave no new database row in any application table.")
