"""F0 presentation contracts. No database or authority claim is made here.

Real Django client dispatch/templates run. Authentication's existing middleware
is supplied an explicit identity double; event reads/writes are service doubles.
The unchanged governed paths still require the PostgreSQL rehearsal before peek 0.
"""
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse
from core.ui import participation
from core import views

class Markup(HTMLParser):
    """Small test-only element reader, independent of attribute order and spacing."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack), "text": ""}
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node["text"] += data

    def select(self, tag, *, within=None, classes=(), **attrs):
        return [node for node in self.nodes if node["tag"] == tag
                and (within is None or any(parent is within for parent in node["parents"]))
                and set(classes).issubset(node["attrs"].get("class", "").split())
                and all(key in node["attrs"] and node["attrs"][key] == value for key, value in attrs.items())]

@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies", ALLOWED_HOSTS=["testserver"])
class F0PresentationTests(SimpleTestCase):
    def setUp(self):
        self.user=SimpleNamespace(is_authenticated=True,is_active=True,get_full_name=lambda:"Alex Morgan")
        self.identity=SimpleNamespace(display_name="Alex Morgan",access_state="active")
        self.auth=patch("django.contrib.auth.middleware.get_user",return_value=self.user)
        memberships = patch("src.intevia.services.organism_membership_service.OrganismMembershipReader.list_own_memberships", return_value=[])
        memberships.start()
        self.addCleanup(memberships.stop)
        self.binding=patch("core.session.IdentityAccessMiddleware._valid_identity",return_value=self.identity)
        self.auth.start();self.binding.start();self.addCleanup(self.auth.stop);self.addCleanup(self.binding.stop)
        self.event=SimpleNamespace(event_id="event:f0",title="Fictional event",description="An example.",resources=[],
            registration=SimpleNamespace(message="Your registration message stays intact.",history_available=False),
            attendance=SimpleNamespace(message="Your attendance message stays intact.",history_available=True))
        self.home=SimpleNamespace(display_name="Alex Morgan",events=[self.event])
        self.history=SimpleNamespace(heading="Registration record",entries=[SimpleNamespace(message="A registration record was created.",occurred_at="2026-11-02")],event_id=self.event.event_id,event_title=self.event.title)

    def test_01_existing_pages_keep_records_forms_and_messages(self):
        """Existing pages retain service content, read-only routes and registration POST/CSRF contracts."""
        with patch.object(views.EventReadService,"home",return_value=self.home),patch.object(views.EventReadService,"present_event",return_value=self.event),patch.object(views.EventReadService,"registration_history",return_value=self.history),patch.object(views.EventReadService,"attendance_history",return_value=self.history):
            cases=[("shell",[],"Alex Morgan"),("event-list",[],self.event.title),("event-detail",[self.event.event_id],self.event.registration.message),("attendance-detail",[self.event.event_id],self.event.attendance.message),("registration-history",[self.event.event_id],self.history.entries[0].message),("attendance-history",[self.event.event_id],self.history.entries[0].message),("registration-detail",[self.event.event_id,"registration:f0"],self.history.entries[0].message)]
            for route,args,expected in cases:
                with self.subTest(route=route):
                    response=self.client.get(reverse(route,args=args))
                    self.assertContains(response,expected,msg_prefix="The moved page must retain its service-provided content.")
                    self.assertIn("no-store",response.headers.get("Cache-Control",""),"Personal pages must remain uncacheable.")
                    self.assertEqual(self.client.post(reverse(route,args=args)).status_code,405,"Reading a page must not accept a write.")
            page=self.client.get(reverse("event-detail",args=[self.event.event_id]))
            self.assertContains(page,'action="'+reverse("event-register-self",args=[self.event.event_id])+'"',msg_prefix="Registration must retain its original POST destination.")
            self.assertContains(page,'name="csrfmiddlewaretoken"',msg_prefix="The registration form must keep CSRF protection.")
            self.event.registration.history_available=True
            page=self.client.get(reverse("event-detail",args=[self.event.event_id]))
            self.assertNotContains(page,"Create my registration record",msg_prefix="An existing registration must not gain another create button.")
            self.assertContains(page,reverse("registration-history",args=[self.event.event_id]),msg_prefix="The original history link must remain available.")
            with patch.object(views.EventReadService,"present_event",side_effect=views.EventNotVisible):
                self.assertEqual(self.client.get(reverse("event-detail",args=[self.event.event_id])).status_code,404,"A hidden Event must still be hidden.")
            for outcome,message in views.REGISTRATION_OUTCOME_MESSAGES.items():
                with self.subTest(outcome=outcome),patch.object(views.EventSelfRegistrationService,"attempt",return_value=outcome) as attempt:
                    response=self.client.post(reverse("event-register-self",args=[self.event.event_id]),follow=True)
                    self.assertContains(response,message,msg_prefix="Service outcome wording must not change during the visual move.")
                    attempt.assert_called_once_with(credential=self.user,identity=self.identity,event_id=self.event.event_id)

    def test_02_navigation_identity_and_honest_destinations(self):
        """Unrestricted pages retain five tabs; restricted pages retain sign-out and plain title without navigation loops."""
        for route in ["f0-explore","f0-organisation","f0-history","f0-you"]:
            response=self.client.get(reverse(route));self.assertEqual(response.status_code,200,"Every navigation destination must open.")
            body=response.content.decode();markup=Markup(body)
            navs=markup.select("nav", **{"aria-label": "Primary navigation"})
            self.assertEqual(len(navs),1,"Each F0 destination must expose one labelled primary navigation region.")
            links=markup.select("a",within=navs[0],classes=("nav-item",))
            self.assertEqual(len(links),5,"The five main navigation positions must remain stable.")
            self.assertEqual(links[0]["text"].strip(),"Home","The first primary navigation slot must remain Home.")
            profiles=markup.select("a",classes=("profile-link",))
            self.assertEqual(len(profiles),1,"The signed-in identity bar must retain one account initials link.")
            self.assertEqual(profiles[0]["text"].strip(),"AM","The account badge must use first and last initials.")
            headers=markup.select("header")
            self.assertEqual(len(headers),1,"Each F0 destination must retain one identity header.")
            self.assertNotIn('Sign out',headers[0]["text"],"Sign out belongs inside You, not the identity bar.")
            mains=markup.select("main",id="main-content")
            self.assertEqual(len(mains),1,"The destination must retain its main content region.")
            self.assertTrue(markup.select("a",within=mains[0],classes=() if route == "f0-you" else ("button",),href=reverse("event-list")),"The signed-in page must offer an Events action in its content, not only in the domain bar.")
        # TEMPORARY F0-TEMP-02: VC retires this prose assertion when website slice 1 lands.
        self.assertContains(self.client.get(reverse("f0-organisation")),"Join or found an organisation",msg_prefix="A learner needs to know organisations will be available.")
        self.assertContains(self.client.get(reverse("f0-you")),"Confirm sign out",msg_prefix="Sign-out confirmation must be available without JavaScript.")
        self.identity.access_state="restricted"
        response=self.client.get(reverse("restricted"))
        self.assertContains(response,"<title>Access restricted | INTEVIA</title>",html=False,msg_prefix="The restricted page title must contain only its label, without sign-out markup or a CSRF token.")
        markup=Markup(response.content.decode())
        self.assertEqual(markup.select("nav",**{"aria-label":"Primary navigation"}),[],"The restricted page must hide the tab bar rather than offer links that bounce back.")
        self.assertEqual(markup.select("nav",**{"aria-label":"Domains"}),[],"The restricted page must hide the domain row.")
        self.assertEqual(markup.select("a",classes=("brand",)),[],"The restricted page brand must not link back to Home.")
        self.assertTrue(markup.select("span",classes=("brand",)),"The restricted page must retain the unlinked INTEVIA brand.")
        self.assertContains(response,"Your governed access is currently restricted.",msg_prefix="The restriction message must remain unchanged.")
        self.assertContains(response,'id="you"',msg_prefix="A restricted person must still reach their account's sign-out section.")
        self.assertContains(response,reverse("logout"),msg_prefix="Restricted access must not trap a signed-in person.")
        self.assertEqual(self.client.get(reverse("f0-you")).url,reverse("restricted"),"F0 must not open a new route through restricted access.")

    def test_03_login_retains_entry_and_visitor_limits(self):
        """Failed sign-in retains accessible input errors; visitors get a sign-in action and restricted account access."""
        with patch("django.contrib.auth.middleware.get_user",return_value=AnonymousUser()):
            response=self.client.post(reverse("login"),{"username":"FictionalName","password":""})
            self.assertContains(response,'value="FictionalName"',msg_prefix="A failed form must keep the entered username.")
            self.assertContains(response,'aria-invalid="true"',msg_prefix="The failed field must be marked for assistive technology.")
            self.assertContains(response,'aria-describedby="id_password_errors"',msg_prefix="A field must identify its error message.")
            self.assertContains(response,"This field is required.",msg_prefix="The original form error must survive the presentation change.")
            self.assertContains(response,'name="csrfmiddlewaretoken"',msg_prefix="Sign-in must retain its CSRF field.")
            markup=Markup(response.content.decode())
            navs=markup.select("nav", **{"aria-label": "Primary navigation"})
            self.assertEqual(len(navs),1,"The visitor sign-in page must expose one primary navigation region.")
            self.assertIn("Sign in",navs[0]["text"],"The visitor's account slot must invite sign-in.")
            response=self.client.get(reverse("f0-history"))
            # TEMPORARY F0-TEMP-03: VC retires this prose assertion when the privacy slice lands.
            self.assertContains(response,"public-marked records",msg_prefix="Visitor history must explain its future boundary.")
            self.assertNotContains(response,"A registration record was created.",msg_prefix="A visitor placeholder must not show a personal record.")
            self.assertEqual(self.client.get(reverse("f0-you")).status_code,302,"Personal account access must require sign-in.")
            for route in ["f0-explore","f0-organisation","f0-history"]:
                markup=Markup(self.client.get(reverse(route)).content.decode())
                actions=markup.select("a",classes=("button","primary"),href=reverse("login"))
                self.assertEqual(len(actions),1,"A visitor placeholder must offer one primary Sign in action.")
                self.assertEqual(actions[0]["text"].strip(),"Sign in","The visitor's primary action must say Sign in.")
                self.assertEqual(markup.select("a",classes=("button",),href=reverse("event-list")),[],"A visitor must not be offered the signed-in Events action.")

    def test_04_catalogue_is_opt_in_and_has_every_state(self):
        """The catalogue is absent when disabled and renders actual component-state markup when enabled."""
        with self.settings(INTEVIA_DESIGN_CATALOGUE=False):
            self.assertEqual(self.client.get(reverse("f0-catalogue")).status_code,404,"The catalogue must be absent when disabled.")
        with self.settings(INTEVIA_DESIGN_CATALOGUE=True):
            response=self.client.get(reverse("f0-catalogue"))
            markup=Markup(response.content.decode())
            self.assertTrue([node for node in markup.select("button",disabled=None) if "aria-busy" not in node["attrs"]],"The catalogue must render a disabled non-loading button; a loading button cannot stand in for it.")
            self.assertTrue(markup.select("button",disabled=None,**{"aria-busy":"true"}),"The enabled catalogue must render a loading button with aria-busy=true.")
            self.assertTrue(markup.select("aside",classes=("notice","refusal"),role="alert"),"The enabled catalogue must render a refusal alert with its notice and refusal classes.")
            self.assertTrue(markup.select("div",classes=("participation-ring",)),"The enabled catalogue must render a participation ring element.")
            self.assertTrue(markup.select("input",**{"aria-invalid":"true"}),"The enabled catalogue must render an invalid field for its error state.")
            tables=markup.select("table")
            self.assertEqual(len(tables),2,"The catalogue must render both the theme-role and contrast tables.")
            for caption in ["Values from the single theme input","Foreground against background"]:
                self.assertTrue(any(node["text"].strip()==caption for table in tables for node in markup.select("caption",within=table)),"The catalogue is missing its named table: "+caption)
            for text in ["FICTIONAL DATA","Abstained","Anonymous participation","Control boundary","Your selection is kept."]:
                self.assertContains(response,text,msg_prefix="The catalogue must retain its fictional-data, answer, contrast and retained-entry explanations.")

    def test_05_anonymous_counts_never_render_people(self):
        """Anonymous participation discards names and retains counts with all four labelled answer symbols."""
        secret_names=[{"name":"Fictional Private Person","answer":"consented"}]
        vote=participation(secret_names,counts={"consented":1,"declined":1,"abstained":1,"pending":1},anonymous=True)
        markup=render_to_string("core/components/participation.html",{"vote":vote})
        self.assertNotIn("Fictional Private Person",markup,"Anonymous output must contain no names, even hidden ones.")
        self.assertEqual(vote["people"],[],"Anonymous presentation data must discard member objects before rendering.")
        for label in ["✓ Consented","✕ Declined","— Abstained","… Not yet"]:
            self.assertIn(label,markup,"Every answer needs a visible word and a non-colour symbol.")
        self.assertIn('participation-ring',markup,"Anonymous voting must use counts, even below the orbit threshold.")

    def test_06_threshold_is_configurable_and_names_remain_in_list(self):
        """The configurable orbit threshold preserves full-name lists and default Voting vocabulary."""
        people=[{"name":f"Fictional Long-Surname {i}","answer":"pending"} for i in range(10)]
        self.assertEqual(participation(people[:9])["mode"],"orbit","Nine participants must retain the orbit.")
        self.assertEqual(participation(people)["mode"],"ring","Ten participants must use the participation ring.")
        with self.settings(INTEVIA_ORBIT_MAX_MEMBERS=2):
            self.assertEqual(participation(people[:3])["mode"],"ring","The threshold must be a setting, not a fixed template decision.")
        markup=render_to_string("core/components/participation.html",{"vote":participation(people[:9])})
        self.assertIn(people[0]["name"],markup,"The list must retain full names when public participant data is supplied.")
        self.assertIn("Voting",markup,"Default interface vocabulary must come from the central lookup.")

    def test_07_refusal_stays_whole_and_next_action_precedes_retained_entry(self):
        """Refusals retain their full message and secondary-link URL before retained-entry detail."""
        message="You cannot change or retire the Coordinator office here. No changes have been made to the office."
        markup=render_to_string("core/components/notice.html",{"kind":"refusal","message":message,"next_url":"/history/","next_label":"Read history","retained":"Your selection is kept."})
        self.assertIn(message,markup,"A refusal must be shown in full without a softened substitute.")
        self.assertIn('notice refusal',markup,"A refusal must use its dedicated error presentation.")
        links=Markup(markup).select("a",classes=("button","secondary"),href="/history/")
        self.assertEqual(len(links),1,"The refusal next step must render one secondary-button link to its supplied history URL.")
        self.assertEqual(links[0]["text"].strip(),"Read history","The refusal next-action link must retain its supplied label.")
        self.assertLess(markup.index("Read history"),markup.index("Your selection is kept."),"The actionable next step must come before retained-entry detail.")

    def test_08_domain_variants_and_local_assets_only(self):
        """Domain scopes enforce sign-in and Event routing/current state; F0 assets use no external resources, network or storage."""
        for style,expected in [("greyed",True),("normal",False)]:
            with self.settings(INTEVIA_OFF_DOMAIN_STYLE=style):
                for scope,route in [("organisation","f0-organisation"),("you","f0-you")]:
                    body=self.client.get(reverse(route)).content.decode()
                    self.assertEqual('domain-item off' in body,expected,"The chosen switched-off domain style must be applied.")
                    if scope == "organisation":
                        events=Markup(body).select("a",classes=("domain-item",),href=reverse("event-list"))
                        self.assertEqual(len(events),1,"The organisation domain row must contain one enabled Events link.")
                        self.assertNotIn("off",events[0]["attrs"].get("class","").split(),"Enabled Events must not be greyed out with unavailable domains.")
                    for domain in ["library","education","events","service","discussion"]:
                        response=self.client.get(reverse("f0-domain",args=[scope,domain]))
                        if scope == "organisation" and domain == "events":
                            self.assertRedirects(response,reverse("event-list"),fetch_redirect_response=False,msg_prefix="The organisation Events domain must redirect to its existing Event list.")
                        else:
                            self.assertEqual(response.status_code,200,"An unfinished signed-in domain must explain its next stage: "+scope+"/"+domain)
        with patch("django.contrib.auth.middleware.get_user",return_value=AnonymousUser()):
            for domain in ["library","education","events","service","discussion"]:
                url=reverse("f0-domain",args=["you",domain])
                self.assertRedirects(self.client.get(url),reverse("login")+"?next="+url,fetch_redirect_response=False,msg_prefix="A visitor must sign in before opening a You-scoped domain: "+domain)
        with patch.object(views.EventReadService,"home",return_value=self.home),patch.object(views.EventReadService,"present_event",return_value=self.event):
            for route,args in [("event-list",[]),("event-detail",[self.event.event_id])]:
                markup=Markup(self.client.get(reverse(route,args=args)).content.decode())
                current=markup.select("a",classes=("domain-item",),**{"aria-current":"page"})
                self.assertEqual(len(current),1,"An Event page must mark exactly one current domain.")
                self.assertEqual(current[0]["attrs"].get("href"),reverse("event-list"),"The current domain on an Event page must be Events.")
        root=Path(__file__).resolve().parents[1]
        for path in sorted((root/"core/templates/core").rglob("*.html")):
            self.assertNotRegex(path.read_text(),r"https?://|(?:href|src)=[\"']\s*//","Templates must not fetch external resources: "+str(path))
        css=(root/"core/static/core/f0.css").read_text()
        self.assertNotRegex(css,r"https?://|url\([\"']?//","CSS must not fetch external resources.")
        js=(root/"core/static/core/f0.js").read_text()
        self.assertNotRegex(js,r"https?://|fetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon","F0 JavaScript must not reference external URLs or initiate network requests.")
        self.assertNotRegex(js,r"localStorage|sessionStorage|indexedDB|document\.cookie|caches\.","F0 must not add browser storage.")

    def test_keyboard_scrolling_reserves_space_for_fixed_navigation(self):
        """Check the served breakpoint rules. Chromium measures actual Tab positions."""
        css = (Path(__file__).resolve().parents[1] / "core/static/core/f0.css").read_text()
        phone, breakpoint, desktop = css.partition("@media (min-width:64rem){")
        self.assertTrue(breakpoint, "The served shell must have its wide navigation breakpoint.")
        self.assertIn("html:has(.main-nav){scroll-padding-bottom:calc(var(--bar) + var(--safe) + var(--space-sm))}",
                      phone, "Phone keyboard scrolling must reserve the bottom navigation and focus gap.")
        self.assertIn("html:has(.domain-nav){scroll-padding-bottom:calc(var(--bar) + var(--domains) + var(--safe) + var(--space-sm))}",
                      phone, "Phone focus must reserve the domain bar even while scrolling has collapsed it.")
        self.assertIn("html:has(.main-nav){scroll-padding-bottom:0}", desktop,
                      "A wide side rail alone must not reserve space below the viewport.")
        self.assertIn("html:has(.domain-nav){scroll-padding-bottom:calc(var(--domains) + var(--space-sm))}",
                      desktop, "Wide keyboard scrolling must reserve the domain bar and focus gap.")


class LinkTargetTests(SimpleTestCase):
    """C1-297. Read the served rule; the VC measures the rendered pixel positions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sheet = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")

    def inline_rule(self):
        # Match the complete selector. An added :only-child would also match
        # the single anchor in "Read <a>the result</a> here".
        pattern = (
            r"(?:^|[{}])main :where\(([^()]*)\) "
            r"a:not\(\.button\):not\(\.nav-item\):not\(\.domain-item\)"
            r":not\(:where\(([^()]*)\)\)\{([^{}]*)\}"
        )
        match = re.search(pattern, self.sheet)
        self.assertIsNotNone(match, "Expected the inline content rule in the served sheet.")
        containers, standalone, body = match.groups()
        return set(containers.split(",")), set(standalone.split(",")), dict(
            declaration.split(":", 1) for declaration in body.split(";")
        )

    def test_fact_links_share_label_line(self):
        containers, _, style = self.inline_rule()
        self.assertTrue({"dt", "dd"} <= containers, "Fact labels and values must admit inline links.")
        self.assertEqual(style.get("display"), "inline")
        self.assertEqual(style.get("padding-block"), "0")
        self.assertEqual(style.get("min-height"), "0")

    def test_sentence_links_follow_the_text(self):
        containers, _, style = self.inline_rule()
        self.assertTrue({"p", "li", "blockquote"} <= containers, "Prose must admit inline links.")
        self.assertEqual(style.get("display"), "inline")
        self.assertEqual(style.get("padding-block"), "0")
        self.assertEqual(style.get("text-decoration-line"), "underline")

    def test_standalone_links_keep_the_touch_target(self):
        _, standalone, _ = self.inline_rule()
        self.assertSetEqual(standalone, {
            ".link-row a",
            ".discussion-topics>li>a", ".circle-list>li>a", ".history-row>a", "nav a",
        }, "Only the standalone action and navigation rows are exempt from inline prose styling.")
        self.assertIn(
            "main a:not(.button):not(.nav-item):not(.domain-item)"
            "{min-height:var(--touch);padding-block:var(--space-sm);display:inline-block}",
            self.sheet,
        )
        self.assertIn("--touch:2.75rem", self.sheet)

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        event = {"event_id": "event:sample", "attendance": {"history_available": True}}
        history = {"event_id": event["event_id"]}
        for template, context, routes in (
            ("event_list", {}, [reverse("shell")]),
            ("event_detail", {"event": event}, [reverse("event-list")]),
            ("attendance_detail", {"event": event}, [
                reverse("attendance-history", args=[event["event_id"]]),
                reverse("event-detail", args=[event["event_id"]]),
            ]),
            ("record_history", {"history": history}, [reverse("event-detail", args=[event["event_id"]])]),
        ):
            with self.subTest(template=template):
                page = Markup(render_to_string(f"core/{template}.html", context, request=request))
                targets = [node["attrs"]["href"] for node in page.select("a")
                           if "link-row" in node["parents"][-1]["attrs"].get("class", "").split()]
                self.assertCountEqual(targets, routes, "Each standalone Event action needs its target.")

    def test_link_only_paragraphs_have_action_containers(self):
        root = Path(__file__).resolve().parents[1]
        from os import walk
        templates = []
        for folder, directories, names in walk(root):
            directory = Path(folder)
            directories[:] = [name for name in directories if not name.startswith(".")
                              and name not in {"node_modules", "__pycache__"}
                              and not (directory / name / "pyvenv.cfg").is_file()]
            if "templates" in directory.relative_to(root).parts:
                templates.extend(directory / name for name in names if name.endswith(".html"))
        templates.sort()
        self.assertTrue(templates, "The check must discover project templates.")
        by_name = {path.as_posix().split("/templates/", 1)[1]: path for path in templates}
        include = re.compile(r"""{%\s*include\s+["']([^"']+)["']([^%]*)%}""")

        def expand(path, parents=()):
            source = path.read_text(encoding="utf-8")
            source = re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}",
                            "", source, flags=re.DOTALL)

            def fragment(match):
                name, arguments = match.groups()
                if name == "core/components/button.html":
                    return "<a>action</a>" if re.search(r"\bhref=", arguments) else "<button>action</button>"
                child = by_name.get(name)
                if child and child not in (*parents, path):
                    return expand(child, (*parents, path))
                return ""

            return include.sub(fragment, source)

        def unmarked_rows(source):
            page = Markup(source)
            missing = []
            for tag in ("p", "li"):
                for row in page.select(tag):
                    links = page.select("a", within=row)
                    link_text = "".join(link["text"] for link in links)
                    if not links or "".join(row["text"].split()) != "".join(link_text.split()):
                        continue
                    def protected(link):
                        parents = link["parents"]
                        if any(n["tag"] == "nav" or "link-row" in n["attrs"].get("class", "").split() for n in parents):
                            return True
                        if "history-row" in parents[-1]["attrs"].get("class", "").split():
                            return True
                        return (parents[-1]["tag"] == "li" and len(parents) >= 2
                                and bool({"discussion-topics", "circle-list"} & set(parents[-2]["attrs"].get("class", "").split())))
                    if not all(protected(link) for link in links):
                        missing.append((tag, row["text"].strip()))
            return missing

        probe = '<p><a>First</a></p><li><a>Second</a></li><li>See <a>also</a>.</li>'
        self.assertEqual(unmarked_rows(probe), [("p", "First"), ("li", "Second")])
        missing = []
        for path in templates:
            source = re.sub(r"{%.*?%}", "", expand(path), flags=re.DOTALL)
            missing.extend((str(path.relative_to(root)), tag, text) for tag, text in unmarked_rows(source))
        self.assertEqual(missing, [], "Link-only paragraphs and list items need standalone action targets.")


class PreparationPromptStyleTests(SimpleTestCase):
    def test_prompt_has_teal_stripe_and_surface_in_served_css(self):
        sheet = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        rule = re.search(r"\.card\.prompt\{([^}]+)\}", sheet)
        self.assertIsNotNone(rule)
        style = dict(item.split(":", 1) for item in rule[1].split(";"))
        self.assertEqual(style.get("border-left-color"), "var(--color-accent)")
        self.assertEqual(style.get("background"), "var(--color-accent-soft)")

    def test_card_final_action_row_has_no_bottom_margin(self):
        sheet = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        self.assertIn(".card>.discussion-actions:last-child{margin-block-end:0}", sheet)
        rule = re.search(r"\.discussion-actions\{([^}]+)\}", sheet)
        self.assertIn("margin-block:32px", rule[1])
        self.assertIn("flex-wrap:wrap", rule[1])


class PostQuoteStyleTests(SimpleTestCase):
    def test_served_quote_rule_disclosure_and_touch_target(self):
        sheet = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        def declarations(selector):
            match = re.search(re.escape(selector) + r"\{([^}]+)\}", sheet)
            self.assertIsNotNone(match, selector)
            return dict(pair.split(":", 1) for pair in match[1].split(";"))

        quote = declarations(".post-quote")
        self.assertEqual(quote.get("border-inline-start"), "3px solid var(--color-line)")
        self.assertEqual(quote.get("padding-inline-start"), "var(--space-md)")
        self.assertEqual(declarations(".post-quote summary").get("min-height"), "var(--touch)")
        self.assertIn("--touch:2.75rem", sheet)
        self.assertEqual(declarations(".post-quote summary").get("grid-row"), "2")
        hidden = ".post-quote:has(>details[open])>.quote-preview,.post-quote details[open] .quote-more,.post-quote details:not([open]) .quote-less"
        self.assertIn(hidden + "{display:none}", sheet)

    def test_quote_summary_uses_a_pointer(self):
        sheet = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        rule = re.search(r"\.post-quote summary\{([^}]+)\}", sheet)
        self.assertIsNotNone(rule, "The served stylesheet must style the quote control.")
        declarations = dict(pair.split(":", 1) for pair in rule[1].split(";"))
        self.assertEqual(declarations.get("cursor"), "pointer", "The quote control must show the pointer.")


class CardFootStyleTests(SimpleTestCase):
    def test_last_text_has_no_extra_margin_in_served_cards(self):
        sheet = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        selector = "@scope(.card){:is(p,blockquote):last-child:not(:where(:scope :not(:last-child) *))"
        rule = re.search(re.escape(selector) + r"\{([^}]+)\}", sheet)
        self.assertIsNotNone(rule, "The served rule must reach only the final text branch of each card.")
        declarations = dict(pair.split(":", 1) for pair in rule[1].split(";"))
        self.assertEqual(declarations.get("margin-block-end"), "0", "Final text must end at the card padding.")
