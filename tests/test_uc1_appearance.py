"""Native witnesses for the personal colour preference."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core.models import Identity
from tests.s015_0021_support import create_test_identity, register_test_learner, seed_identity
from tests.test_f0_ui import Markup


class AppearanceMigrationTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def test_existing_identity_follows_device(self):
        """0112 gives old and new identities the device default. The model and database admit
        device, light and dark; a fourth choice is refused without changing the stored preference.
        """
        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration("core", "0112_identity_colour_mode")
        parent = executor.loader.project_state(migration.dependencies)
        # Exercise only this migration, even when another migration follows it.
        executor.unapply_migration(parent, migration)
        try:
            with connection.cursor() as cursor:
                existing = seed_identity(cursor, "uc1-existing")
        finally:
            executor.apply_migration(parent, migration)

        reader = connection.copy()
        self.addCleanup(reader.close)

        def stored(pk):
            with reader.cursor() as cursor:
                cursor.execute("SELECT colour_mode FROM public.core_identity WHERE id = %s", [pk])
                return cursor.fetchall()

        self.assertEqual(stored(existing), [("device",)],
                         "A pre-existing identity must follow the device after upgrade.")
        new = create_test_identity(username="uc1-appearance-new")
        self.assertEqual((new.colour_mode, stored(new.pk)), ("device", [("device",)]),
                         "A newly created identity must follow the device by default.")
        with connection.cursor() as cursor:
            sql_created = seed_identity(cursor, "uc1-sql-default")
        self.assertEqual(stored(sql_created), [("device",)],
                         "An insert that omits the preference must receive the database default.")

        field = Identity._meta.get_field("colour_mode")
        for mode in ("light", "dark", "device"):
            with self.subTest(mode=mode):
                try:
                    field.clean(mode, new)
                    Identity.objects.filter(pk=new.pk).update(colour_mode=mode)
                except (ValidationError, IntegrityError) as exc:
                    self.fail(f"The model and database must accept the colour choice {mode!r}: {exc}")
                self.assertEqual(stored(new.pk), [(mode,)],
                                 "The requested colour choice must be visible on a second connection.")

        with self.assertRaises(ValidationError, msg="The model must reject an unknown colour choice."):
            field.clean("sepia", new)
        with self.assertRaises(IntegrityError, msg="The database must reject an unknown colour choice.") as refusal:
            with transaction.atomic():
                Identity.objects.filter(pk=new.pk).update(colour_mode="sepia")
        diagnostic = refusal.exception.__cause__.diag
        self.assertEqual((diagnostic.sqlstate, diagnostic.constraint_name), ("23514", "identity_colour_mode_valid"),
                         "An unknown colour choice must be refused by the named colour constraint.")
        self.assertEqual(stored(new.pk), [("device",)],
                         "A refused fourth choice must preserve the last committed colour preference.")


@override_settings(ALLOWED_HOSTS=["testserver"],
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AppearanceViewTests(TransactionTestCase):
    # Disposable registration and sign-in value used only by these witnesses.
    fixture_password = "governed-test-password"

    def setUp(self):
        self.you = reverse("f0-you")
        self.reader = connection.copy()
        self.addCleanup(self.reader.close)

    def register(self, name):
        return register_test_learner(username=name, password=self.fixture_password)

    def private(self, response):
        directives = {word.strip().lower() for word in response.headers.get("Cache-Control", "").split(",")}
        self.assertTrue({"private", "no-store"} <= directives, "A personal appearance response must be private and no-store.")
        vary = {word.strip().lower() for word in response.headers.get("Vary", "").split(",")}
        self.assertIn("cookie", vary,
                      "A personal appearance response must vary by cookie.")
        self.assertFalse(set(response.cookies) - {settings.CSRF_COOKIE_NAME, settings.SESSION_COOKIE_NAME},
                         "An appearance response may set only the existing CSRF and session cookies.")

    def form_token(self, response, *, action=None):
        page = Markup(response.content.decode())
        forms = page.select("form", method="post", **({"action": action} if action else {}))
        self.assertEqual(len(forms), 1, "There must be exactly one POST form for the requested action.")
        tokens = page.select("input", within=forms[0], name="csrfmiddlewaretoken")
        self.assertEqual(len(tokens), 1, "The submitted form must carry one CSRF token.")
        return tokens[0]["attrs"]["value"]

    def sign_in(self, person):
        client = Client(enforce_csrf_checks=True)
        url = reverse("login")
        token = self.form_token(client.get(url))
        response = client.post(url, {"username": person.credential.username, "password": self.fixture_password,
                                     "csrfmiddlewaretoken": token})
        self.assertEqual((response.status_code, response.get("Location")), (302, reverse("shell")),
                         "The registered person must establish a fresh bound session through the sign-in form.")
        return client

    def modes(self, first, second):
        with self.reader.cursor() as cursor:
            cursor.execute("SELECT id, colour_mode FROM public.core_identity WHERE id IN (%s, %s)",
                           [first.pk, second.pk])
            rows = dict(cursor.fetchall())
        return rows.get(first.pk), rows.get(second.pk)

    def organisation_record(self, organisation):
        with self.reader.cursor() as cursor:
            cursor.execute("SELECT head_event_id, to_jsonb(o) FROM public.core_livingorganism o WHERE id=%s",
                           [organisation.pk])
            return cursor.fetchone()

    def page_mode(self, response, expected):
        self.assertEqual(response.status_code, 200, "The appearance must be read from a successful page response.")
        page = Markup(response.content.decode())
        roots = page.select("html")
        sheets = page.select("link", rel="stylesheet")
        self.assertEqual(len(roots), 1, "The response must contain one opening HTML element.")
        self.assertTrue(bool(sheets), "The page must load the shared stylesheet.")
        self.assertLess(page.nodes.index(roots[0]), page.nodes.index(sheets[0]),
                        "The server must emit the mode on the root before the stylesheet loads.")
        self.assertEqual(roots[0]["attrs"].get("data-mode"), None if expected == "device" else expected,
                         "The opening HTML must express the signed-in choice and leave device mode unforced.")
        return page

    def you_form(self, client, expected):
        response = client.get(self.you)
        self.private(response)
        page = self.page_mode(response, expected)
        controls = page.select("input", type="radio", name="colour_mode")
        self.assertEqual([node["attrs"]["value"] for node in controls], ["device", "light", "dark"],
                         "You must offer exactly the three registered colour choices.")
        self.assertEqual([node["parents"][-1]["text"].strip() for node in controls],
                         ["Use device setting", "Light", "Dark"], "Every radio must have its own plain-English label.")
        self.assertEqual([node["attrs"]["value"] for node in controls if "checked" in node["attrs"]], [expected],
                         "Exactly the saved choice must be checked on You.")
        return self.form_token(response, action=self.you)

    def test_you_saves_only_the_signed_in_identity(self):
        """You saves only A's mode; B and the organisation head survive. A fresh sign-in
        reads dark before the stylesheet, then resetting to device clears the override.
        """
        from tests.test_law_acts import ordinary_birth

        organisation, second = ordinary_birth()
        first = self.register("uc1-appearance-first")
        Identity.objects.filter(pk=second.pk).update(colour_mode="light")
        self.assertEqual(self.modes(first, second), ("device", "light"), "The two people must start in distinct modes.")
        record = self.organisation_record(organisation)
        self.assertIsNotNone(record[0], "The organisation must start with a real recorded head.")
        client = self.sign_in(first)
        token = self.you_form(client, "device")
        response = client.post(self.you, {"colour_mode": "dark", "csrfmiddlewaretoken": token,
                                          "identity_id": str(second.identity_id)})
        a, b = self.modes(first, second)
        self.assertEqual(b, "light", "Saving A’s appearance must preserve B’s previous mode.")
        self.assertEqual(a, "dark", "A valid save must commit dark for the signed-in person.")
        self.assertEqual(self.organisation_record(organisation), record, "Saving a personal mode must preserve the organisation head.")
        self.assertEqual((response.status_code, response.get("Location")), (302, self.you),
                         "Saving the mode must redirect to You.")
        self.private(response)

        fresh = self.sign_in(first)
        self.page_mode(fresh.get(reverse("f0-history")), "dark")
        token = self.you_form(fresh, "dark")
        reset = fresh.post(self.you, {"colour_mode": "device", "csrfmiddlewaretoken": token})
        self.assertEqual((reset.status_code, reset.get("Location")), (302, self.you), "Resetting to device must redirect to You.")
        self.private(reset)
        self.you_form(fresh, "device")
        self.assertEqual(self.modes(first, second), ("device", "light"), "Resetting to device must leave B's choice intact.")
        self.assertEqual(self.organisation_record(organisation), record, "Resetting a personal mode must preserve the organisation head.")

    def test_appearance_requires_valid_own_post(self):
        """A GET, missing CSRF token, signed-out POST or invalid choice preserves both
        accounts. Only a signed-in POST with a valid choice and CSRF token saves it.
        """
        first = self.register("uc1-appearance-requester")
        second = self.register("uc1-appearance-control")
        client = self.sign_in(first)
        valid = client.post(self.you, {"colour_mode": "dark", "csrfmiddlewaretoken": self.you_form(client, "device")})
        self.assertEqual((valid.status_code, valid.get("Location")), (302, self.you), "The positive control must save and redirect to You.")
        self.private(valid)
        original = ("dark", "device")
        self.assertEqual(self.modes(first, second), original, "The positive control must commit only the sign-in owner's choice.")

        # Equal final values alone would miss a read that wrote the old preference back.
        updates = []

        def observe(execute, sql, params, many, context):
            words = str(sql).lower()
            if words.lstrip().startswith("update ") and "core_identity" in words and "colour_mode" in words:
                updates.append(True)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(observe):
            query = client.get(self.you, {"colour_mode": "light"})
        self.assertFalse(updates, "Reading You must not write a colour preference, even if its value stays the same.")
        self.assertEqual(self.modes(first, second), original, "A GET query must leave both preferences in place.")
        self.page_mode(query, "dark")
        self.private(query)

        missing = client.post(self.you, {"colour_mode": "light"})
        self.assertEqual(missing.status_code, 403, "A POST with its CSRF field missing must be refused.")
        self.private(missing)
        self.assertEqual(self.modes(first, second), original, "CSRF refusal must leave both preferences in place.")

        visitor = Client(enforce_csrf_checks=True)
        login_page = visitor.get(reverse("login"))
        self.page_mode(login_page, "device")
        unsigned = visitor.post(self.you, {"colour_mode": "light", "csrfmiddlewaretoken": self.form_token(login_page)})
        self.assertEqual((unsigned.status_code, unsigned.get("Location")), (302, reverse("login") + "?next=" + self.you),
                         "A signed-out POST with a valid CSRF token must be sent to sign-in.")
        self.private(unsigned)
        self.assertEqual(self.modes(first, second), original, "A signed-out request must leave both preferences in place.")

        token = self.you_form(client, "dark")
        for value in ("sepia", "", ["light", "dark"]):
            with self.subTest(value=value):
                invalid = client.post(self.you, {"colour_mode": value, "csrfmiddlewaretoken": token})
                self.assertEqual(self.modes(first, second), original, "An unlisted mode must leave the previous preference in place.")
                self.assertEqual(invalid.status_code, 400, "An invalid or ambiguous choice must return the appearance form with an error.")
                self.private(invalid)
                self.assertTrue(bool(Markup(invalid.content.decode()).select("p", role="alert")),
                                "The invalid-choice form must explain the refusal.")

    def test_appearance_refuses_an_epoch_change_at_the_update(self):
        """Advance the fixture epoch after middleware accepts the session and immediately before the guarded write."""
        first = self.register("uc1-mode-race")
        second = self.register("uc1-mode-other")
        Identity.objects.filter(pk=second.pk).update(colour_mode="light")
        client = self.sign_in(first)
        token = self.you_form(client, "device")
        self.assertEqual(self.modes(first, second), ("device", "light"),
                         "The two people must start with distinct committed colour choices.")
        cuts = []

        def change_epoch(execute, sql, params, many, context):
            if sql.lstrip().startswith('UPDATE "core_identity" SET "colour_mode" ='):
                # The request holds its earlier Identity. A separate committed write makes that epoch stale.
                with self.reader.cursor() as cursor:
                    cursor.execute("UPDATE public.core_identity SET access_epoch = access_epoch + 1 "
                                   "WHERE id = %s AND access_epoch = %s", [first.pk, first.access_epoch])
                    cuts.append(cursor.rowcount)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(change_epoch):
            response = client.post(self.you, {"colour_mode": "dark", "csrfmiddlewaretoken": token})

        self.assertEqual(cuts, [1], "The request must reach the real appearance UPDATE after the epoch advances once.")
        self.assertEqual(response.status_code, 403, "A session that becomes stale at the appearance UPDATE must receive 403.")
        self.private(response)
        self.assertEqual(self.modes(first, second), ("device", "light"),
                         "A stale appearance request must preserve both people's committed choices.")
        alerts = Markup(response.content.decode()).select("p", role="alert")
        self.assertEqual([node["text"].strip() for node in alerts],
                         ["Your session has changed. Sign in again to save your appearance."],
                         "The refusal must tell the person to sign in again before saving appearance.")


@override_settings(ALLOWED_HOSTS=["testserver"], DEBUG=False)
class AppearanceCsrfTests(SimpleTestCase):
    def test_other_forms_use_django_and_you_keeps_its_five_part_refusal(self):
        from django.views.csrf import csrf_failure

        client = Client(enforce_csrf_checks=True)
        ordinary = client.post(reverse("login"), {})
        expected = csrf_failure(ordinary.wsgi_request, reason="CSRF cookie not set.")
        self.assertEqual((ordinary.status_code, ordinary.content), (403, expected.content),
                         "Other forms must return Django's own CSRF refusal page.")
        you = client.post(reverse("f0-you"), {})
        self.assertEqual(you.status_code, 403, "The You form must reject a missing CSRF token.")
        page = Markup(you.content.decode())
        parts = [(node["tag"], node["text"].strip()) for alert in page.select("aside", role="alert")
                 for node in page.nodes if node["parents"] and node["parents"][-1] is alert]
        self.assertEqual(parts, [
            ("h1", "The form could not be submitted."), ("p", "Its security check did not pass."),
            ("p", "This request saved nothing."), ("p", "Reopen the page and submit a fresh form."),
            ("p", "Contact support if the new form is refused."),
        ], "The You CSRF page must give all five refusal parts in order.")
