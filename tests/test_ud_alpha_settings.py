"""U-D P1: the Alpha settings' import-time gates. No database is used or needed.

UD-01 (T1): the database gate refuses before any connection (rule page G1).
UD-02 (T2): localhost only, the wake marker, and no secret key of its own (G2, G7, G8).

Each case imports ``intevia.settings`` and ``intevia.alpha_settings`` afresh under a
stated environment, then puts the runner's own modules back. The expected reason
sentences are written here; none is read from the module under test.
"""

import importlib
import os
import secrets
import sys
from unittest import mock

from django.core.exceptions import DisallowedHost, ImproperlyConfigured
from django.db.backends.postgresql import base as postgresql
from django.test import RequestFactory, SimpleTestCase, override_settings

BASE = "intevia.settings"
ALPHA = "intevia.alpha_settings"
_ABSENT = object()

NAME_REASON = "The database name is not of the form intevia_alpha_throwaway_<label>"
HOST_REASON = "The database host is not this computer"
OVERRIDE_REASON = "A libpq override (PGHOSTADDR, PGSERVICE or PGSERVICEFILE) is set"
STARTED = "Alpha did not start."
UNCHANGED = "No connection was opened and nothing was changed."
HELP = "The Vision Chamber can help with this deployment."
# Each refusal's own next step (the fourth of the five parts), written here by hand.
NEXT_STEPS = {
    NAME_REASON: "Set INTEVIA_POSTGRES_DB to your disposable Alpha database and start again.",
    HOST_REASON: "Set INTEVIA_POSTGRES_HOST to 127.0.0.1, or leave it unset, and start again.",
    OVERRIDE_REASON: "Remove those three variables from the environment and start again.",
}
# Invented values that occur nowhere in a refusal's fixed words, so a leak is detectable.
MARK = "zq7"


def environment(**changes):
    """A complete, valid Alpha environment of invented values; None removes a key."""
    values = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
        "INTEVIA_DATABASE_ENGINE": "postgresql",
        "INTEVIA_POSTGRES_DB": "intevia_alpha_throwaway_film1",
        "INTEVIA_POSTGRES_USER": MARK + "_fixture_user",
        "INTEVIA_POSTGRES_PASSWORD": MARK + secrets.token_urlsafe(12),
    }
    values.update(changes)
    return {key: value for key, value in values.items() if value is not None}


class AlphaSettingsTests(SimpleTestCase):
    def load(self, env):
        """Import both modules afresh under exactly ``env``.

        Returns (module or None, refusal or None, connection attempts).
        """
        package = importlib.import_module("intevia")
        kept_modules = {name: sys.modules.get(name, _ABSENT) for name in (BASE, ALPHA)}
        kept_attributes = {
            name: getattr(package, name, _ABSENT) for name in ("settings", "alpha_settings")
        }
        attempts = []

        def trap(*args, **kwargs):
            attempts.append(1)
            raise AssertionError("A settings import must not open a database connection.")

        try:
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                postgresql.DatabaseWrapper, "get_new_connection", trap
            ), mock.patch.object(postgresql.Database, "connect", trap):
                for name in (BASE, ALPHA):
                    sys.modules.pop(name, None)
                try:
                    return importlib.import_module(ALPHA), None, len(attempts)
                except ImproperlyConfigured as refusal:
                    return None, refusal, len(attempts)
        finally:
            for name, module in kept_modules.items():
                if module is _ABSENT:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
            for name, value in kept_attributes.items():
                if value is _ABSENT:
                    if hasattr(package, name):
                        delattr(package, name)
                else:
                    setattr(package, name, value)

    def assert_admitted(self, env, what):
        module, refusal, attempts = self.load(env)
        self.assertIsNone(refusal, f"{what} must load the Alpha settings.")
        self.assertEqual(attempts, 0, f"{what} must not open a database connection at import.")
        return module

    def assert_refused(self, env, reason, what):
        module, refusal, attempts = self.load(env)
        self.assertIsNone(module, f"{what} must not load the Alpha settings.")
        self.assertIsNotNone(refusal, f"{what} must be refused at import.")
        text = str(refusal)
        self.assertIn(reason, text, f"{what} must be refused for its own reason, not another.")
        for other_reason in (NAME_REASON, HOST_REASON, OVERRIDE_REASON):
            if other_reason != reason:
                self.assertNotIn(
                    other_reason, text, f"{what}: the refusal must not also give another refusal's reason."
                )
        for part in (STARTED, UNCHANGED, HELP):
            self.assertIn(part, text, f"{what}: the refusal must keep its five parts.")
        for own_reason, next_step in NEXT_STEPS.items():
            if own_reason == reason:
                self.assertIn(
                    next_step, text, f"{what}: the refusal must say what to do next about this refusal."
                )
            else:
                self.assertNotIn(
                    next_step, text, f"{what}: the refusal must not give another refusal's next step."
                )
        self.assertNotIn(MARK, text, f"{what}: a refusal must never show a configuration value.")
        self.assertNotIn(
            env.get("DJANGO_SECRET_KEY", MARK), text, f"{what}: a refusal must never show the key."
        )
        self.assertEqual(attempts, 0, f"{what} must be refused before any connection attempt.")

    def test_01_database_gate_refuses_before_any_connection(self):
        """UD-01: only an Alpha-named database on a loopback host loads; the rest refuse first."""
        default = self.assert_admitted(environment(), "The base's default host")
        self.assertEqual(
            default.DATABASES["default"]["HOST"],
            "127.0.0.1",
            "With no host given, the Alpha must use the base's numeric loopback default.",
        )
        for host in ("127.0.0.1", "localhost", "::1"):
            module = self.assert_admitted(
                environment(INTEVIA_POSTGRES_HOST=host), f"The loopback host {host!r}"
            )
            self.assertEqual(
                module.DATABASES["default"]["HOST"], host, "The stated loopback host must be used as given."
            )
        for name in (
            "intevia_alpha_throwaway_a",
            "intevia_alpha_throwaway_0",
            "intevia_alpha_throwaway__",
            "intevia_alpha_throwaway_film_2026_11",
        ):
            module = self.assert_admitted(
                environment(INTEVIA_POSTGRES_DB=name), f"The Alpha name {name!r}"
            )
            self.assertEqual(
                module.DATABASES["default"]["NAME"], name, "The stated Alpha name must be used as given."
            )

        for name in (
            "intevia_alpha_throwaway_",  # the bare prefix
            "intevia_alpha_throwaway",
            "xintevia_alpha_throwaway_a",  # the prefix inside a longer name
            "intevia_alpha_throwaway_A",  # a capital
            "intevia_alpha_throwaway_a-b",  # a hyphen
            "intevia_alpha_throwaway_a b",  # a space
            "intevia_alpha_throwaway_a\n",  # a final newline
            "intevia_alpha_throwaway_é",  # a letter outside ASCII
            "intevia",  # a protected name
            "postgres",
            "intevia_peek_throwaway_a",
            "test_intevia_living_organism_a1",  # a valid legacy test name: admitted elsewhere, not here
            MARK + "_private_name",
        ):
            self.assert_refused(
                environment(INTEVIA_POSTGRES_DB=name), NAME_REASON, f"The database name {name!r}"
            )

        for host in (
            "",
            "LOCALHOST",
            "localhost.",
            "127.0.0.2",
            "0.0.0.0",
            "192.168.1.20",
            "alpha-desktop",
            "/var/run/postgresql",
            MARK + "-host.invalid",
        ):
            self.assert_refused(
                environment(INTEVIA_POSTGRES_HOST=host), HOST_REASON, f"The database host {host!r}"
            )

        for variable, value in (
            ("PGHOSTADDR", "203.0.113.77"),
            ("PGHOSTADDR", ""),
            ("PGSERVICE", MARK + "_service"),
            ("PGSERVICEFILE", "/" + MARK + "/pg_service.conf"),
        ):
            for host in (None, "127.0.0.1"):
                self.assert_refused(
                    environment(INTEVIA_POSTGRES_HOST=host, **{variable: value}),
                    OVERRIDE_REASON,
                    f"{variable} set beside a valid name and host {host!r}",
                )
        loaded, address_refusal, _attempts = self.load(environment(PGHOSTADDR="203.0.113.77"))
        self.assertIsNone(loaded, "An override address must not load the Alpha settings.")
        self.assertIsNotNone(
            address_refusal, "An override address must be refused at import, before its refusal is read."
        )
        self.assertNotIn(
            "203.0.113.77",
            str(address_refusal),
            "A refusal must never show the override's address.",
        )

        # Neither a valid name nor a valid host rescues the other.
        self.assert_refused(
            environment(INTEVIA_POSTGRES_HOST=MARK + "-host.invalid"),
            HOST_REASON,
            "A valid Alpha name on a foreign host",
        )
        self.assert_refused(
            environment(INTEVIA_POSTGRES_DB="intevia", INTEVIA_POSTGRES_HOST="127.0.0.1"),
            NAME_REASON,
            "A protected name on a loopback host",
        )

    def test_02_localhost_only_wake_marker_and_secret_key(self):
        """UD-02: two browser hosts; wake only for the exact marker; no key of its own."""
        key = secrets.token_urlsafe(50)
        module = self.assert_admitted(environment(DJANGO_SECRET_KEY=key), "A valid Alpha environment")

        self.assertEqual(
            module.ALLOWED_HOSTS,
            ["127.0.0.1", "localhost"],
            "The Alpha must answer as exactly 127.0.0.1 and localhost: no LAN host, no wildcard.",
        )
        requests = RequestFactory()
        with override_settings(ALLOWED_HOSTS=list(module.ALLOWED_HOSTS), DEBUG=module.DEBUG):
            for host in (
                "localhost",
                "localhost:8000",
                "LOCALHOST:8000",
                "localhost.:8000",
                "127.0.0.1:8000",
            ):
                self.assertTrue(
                    requests.get("/", HTTP_HOST=host).get_host(),
                    f"The browser host {host!r} must be accepted.",
                )
            for host in (
                "[::1]:8000",
                "127.0.0.2:8000",
                "192.168.1.20:8000",
                "alpha-desktop:8000",
                "sub.localhost:8000",
            ):
                with self.assertRaises(
                    DisallowedHost, msg=f"The browser host {host!r} must be refused."
                ):
                    requests.get("/", HTTP_HOST=host).get_host()
        self.assertIs(module.DEBUG, True, "The Alpha states DEBUG itself, for runserver and the outbox route.")

        self.assertIs(
            module.INTEVIA_TIMED_EVENTS_WAKE,
            False,
            "With no marker the wake must be off and the Alpha still running.",
        )
        for marker in ("", "ON", "true", "1", "Rehearsed", "REHEARSED", "rehearsed ", " rehearsed"):
            near = self.assert_admitted(
                environment(INTEVIA_ALPHA_TIMED_EVENTS_WAKE=marker),
                f"The near-miss marker {marker!r} (a near miss is not a refusal)",
            )
            self.assertIs(
                near.INTEVIA_TIMED_EVENTS_WAKE,
                False,
                f"The marker {marker!r} is not the exact word and must leave the wake off.",
            )
        exact = self.assert_admitted(
            environment(INTEVIA_ALPHA_TIMED_EVENTS_WAKE="rehearsed"), "The exact marker"
        )
        self.assertIs(
            exact.INTEVIA_TIMED_EVENTS_WAKE, True, "Only the exact marker turns the wake on."
        )

        self.assertEqual(
            module.SECRET_KEY, key, "The supplied key must survive the Alpha import unchanged."
        )
        for absent in (None, ""):
            loaded, refusal, attempts = self.load(environment(DJANGO_SECRET_KEY=absent))
            self.assertIsNone(
                loaded, "With no key the Alpha must not load: it has no key of its own."
            )
            self.assertIn(
                "DJANGO_SECRET_KEY is required",
                str(refusal),
                "An absent and an empty key must both meet the base's own refusal.",
            )
            self.assertEqual(attempts, 0, "A missing key must be refused before any connection attempt.")

        self.assertIs(
            module.INTEVIA_EMAIL_OUTBOX_PAGE, True, "The Alpha switches the operator outbox page on."
        )
        self.assertEqual(
            module.INTEVIA_EMAIL_OPERATOR_IDENTITIES,
            (),
            "No operator is named unless the environment names one.",
        )
        named = self.assert_admitted(
            environment(INTEVIA_EMAIL_OPERATOR_IDENTITIES=" first-id , second-id ,,"),
            "A comma-separated operator list",
        )
        self.assertEqual(
            named.INTEVIA_EMAIL_OPERATOR_IDENTITIES,
            ("first-id", "second-id"),
            "Operator Identities come from the environment, trimmed, with empty items dropped.",
        )
