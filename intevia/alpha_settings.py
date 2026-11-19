"""Disposable internal Alpha on one PC, served on localhost only. Never a deployment settings module.

Every check below runs when this module is imported, before Django opens any
connection. A refusal says which setting to correct and never shows a value.
Each line names its basis; a setting with no basis is not here (practice item 206).
"""
import os
import re

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403


def _refusal(why, next_step):
    # Five parts (practice item 164): what happened, why, what changed, what to do next, who can help.
    return ImproperlyConfigured(
        f"Alpha did not start. {why} No connection was opened and nothing was changed. "
        f"{next_step} The Vision Chamber can help with this deployment."
    )


_database = DATABASES["default"]  # noqa: F405

# Basis: the Human Governor's ruling of 19 Nov 2026 (Q1 b) and plan v0.7 section 7 step 1.
# The whole name is matched, so an empty label, a capital or a trailing character is refused.
if not re.fullmatch(r"intevia_alpha_throwaway_[a-z0-9_]+", _database["NAME"]):
    raise _refusal(
        "The database name is not of the form intevia_alpha_throwaway_<label>, "
        "where the label is lower-case letters, digits and underscores.",
        "Set INTEVIA_POSTGRES_DB to your disposable Alpha database and start again.",
    )

# Basis: the peek pattern (intevia/peek_settings.py l.16 to 17). An empty host is refused here,
# because it would hand the choice of server to libpq's own environment.
if _database.get("HOST", "") not in {"127.0.0.1", "localhost", "::1"}:
    raise _refusal(
        "The database host is not this computer: it must be 127.0.0.1, localhost or ::1.",
        "Set INTEVIA_POSTGRES_HOST to 127.0.0.1, or leave it unset, and start again.",
    )

# Basis: libpq applies these beneath Django's HOST, so a local-looking host could still
# connect elsewhere. Their presence is refused, whatever they hold.
if any(name in os.environ for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")):
    raise _refusal(
        "A libpq override (PGHOSTADDR, PGSERVICE or PGSERVICEFILE) is set, "
        "so the connection could go somewhere other than this computer.",
        "Remove those three variables from the environment and start again.",
    )

# Basis: runserver serves the committed stylesheet only with DEBUG, and the outbox route
# requires it (intevia/urls.py). Stated here so Alpha does not change when the base does.
DEBUG = True

# Basis: the Human Governor's ruling of 19 Nov 2026, 12:12 (localhost on his PC only; no LAN host).
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# Basis: intevia/settings.py, "Off until the host rehearsal has passed on that host", and the
# ruling of 19 Nov 2026 (Q4 a). D-3 writes the marker only after that rehearsal; any other
# value, or none, leaves the wake off and the Alpha running.
INTEVIA_TIMED_EVENTS_WAKE = os.environ.get("INTEVIA_ALPHA_TIMED_EVENTS_WAKE") == "rehearsed"

# Basis: commission section 3 point 1; the peek pattern. Only named operator Identities may
# inspect the disposable outbox, and none is named unless the environment names one.
INTEVIA_EMAIL_OUTBOX_PAGE = True
INTEVIA_EMAIL_OPERATOR_IDENTITIES = tuple(
    item.strip() for item in os.environ.get("INTEVIA_EMAIL_OPERATOR_IDENTITIES", "").split(",")
    if item.strip()
)

# Basis: the Human Governor's ruling of 19 Nov 2026 (Q3 a: the local email path to the mail catcher
# on this PC) and relay design v1.1 section 6. Only the Alpha selects the relay. It refuses to start,
# before any message is claimed, unless the destination is 127.0.0.1 with no mail user, password,
# TLS or SSL (email_foundation/loopback.py).
EMAIL_BACKEND = "email_foundation.loopback.LoopbackRelayBackend"

# Basis: the Human Governor's ruling of 19 Nov 2026, 12:12 (localhost on his PC only). The relay
# accepts this literal and no other destination.
EMAIL_HOST = "127.0.0.1"

# Basis: relay design v1.1 section 6. 1025 is the mail catcher's (Mailpit's) own default SMTP port,
# where D-3 starts it on 127.0.0.1. A wrong port is a refused connection, not a wrong destination.
EMAIL_PORT = 1025

# Basis: relay design v1.1 section 6. The worker holds the recipient's and the intent's row locks
# across the send (email_foundation/worker.py, deliver), so a silent catcher is given five seconds.
EMAIL_TIMEOUT = 5
