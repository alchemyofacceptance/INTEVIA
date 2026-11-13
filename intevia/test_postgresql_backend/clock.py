"""The INTEVIA Clock on the owned test route: who may install the moving part, who may move it, and who may reverse it.

Three permissions, kept apart.

Installing happens where the owned route runs migrations: on the bootstrap database of a
template build, and on a clone when the route creates and migrates a clone directly. A
template and a control database are never migrated, so nothing is installed on them.

Advancing happens only on the qualified active clone that tests run on. A bootstrap,
template or control database, and any other name, is never an advance target.

Reversing the moving part's migration happens only on a clone, and only while the clock
is at zero. The reverse puts the production faces back and removes the moving part's
routines (the move, which is a procedure, and its functions). It keeps the counter: the
counter is the evidence that the database has held
a clock that can be moved, and production's startup refuses any database that holds it.
Installing again keeps the counter it finds, after comparing it with what the migration
writes: its type, start, increment, minimum, maximum, cache, cycle and ownership, and
the value read by intevia_test_clock_offset().

All three check the owned route's settings, that the connected database is the configured
one, the stage its name belongs to, and that the session's role owns the database. Moving
and reversing also require the connection to be the lifecycle backend's qualified active
clone.

All three then require the database to be bound to this run, by its oid, in one of two ways
and no other. A verification run carries the owned route's receipts (a receipt file and a
run nonce, set together): there the database must be the one this run's receipts record
creating. A rehearsal carries no receipts and no VERIFICATION_ variable at all: there the
database must be the one this run's test runner recorded creating or selecting
(creation.py). Anything in between is refused: a receipt file without a nonce or a nonce
without a file, a verification variable without receipts, and a run with neither receipts
nor a record. A verification run is never silently treated as a rehearsal.

No positive move is made anywhere until every read of the time has been converted.
"""

import os
from datetime import datetime, timedelta

from django.conf import settings
from django.db import Error, connections
from django.utils import timezone

from . import clock_route, creation
from .operations import S015_TEST_ROUTE

COUNTER = "public.intevia_test_clock_offset_us"
REFUSAL_PREFIX = "INTEVIA Clock"
# The move is a procedure whose first act is COMMIT. It is called alone, never inside a transaction block.
MOVE_SQL = "CALL public.intevia_test_clock_advance(%s::bigint, %s::timestamp with time zone, NULL)"
# PostgreSQL's own refusal of that COMMIT inside a transaction block: invalid_transaction_termination.
INSIDE_A_TRANSACTION = "2D000"
INSIDE_A_TRANSACTION_TEXT = ("INTEVIA Clock: the move was asked for inside an open transaction; "
                             "the clock moves only between transactions. Nothing changed.")
INSTALL_STAGES = (clock_route.BOOTSTRAP, clock_route.CLONE)
ADVANCE_STAGES = (clock_route.CLONE,)
REVERSE_STAGES = (clock_route.CLONE,)
# The counter as the moving part's migration writes it. A kept counter is compared with every line here.
COUNTER_AS_MIGRATED = (
    ("type", "bigint"),
    ("start", 0),
    ("increment", 1),
    ("minimum", 0),
    ("maximum", 9007199254740991),
    ("cache", 1),
    ("cycle", False),
    ("ownership", "the database's owner, and no table column"),
    ("value read by intevia_test_clock_offset()", 0),
)
CREATION_EVENTS = ("created", "template_database_created")
# The largest offset the clock can hold, in microseconds: the counter's own maximum.
LARGEST_OFFSET = dict(COUNTER_AS_MIGRATED)["maximum"]
RECEIPT_ENV = "VERIFICATION_DB_RECEIPT"
NONCE_ENV = "VERIFICATION_RUN_NONCE"
# Any variable with this prefix marks a verification run. The verification route sets several.
VERIFICATION_PREFIX = "VERIFICATION_"
# How a run is configured for receipts: both variables, one without the other, or neither.
COMPLETE, PARTIAL, NONE = "complete", "partial", "none"


class ClockRouteRefused(RuntimeError):
    """The owned route refused to install, to move or to reverse the INTEVIA Clock."""


def refusal(purpose, stages, *, owned_settings, configured, connected, owner_is_session_role, receipts,
            receipt_configuration, verification_run, recorded, connected_oid):
    """Why this database may not be used for this purpose, or None. A pure decision over stated facts.

    receipt_configuration is COMPLETE, PARTIAL or NONE. receipts is a list of (event, name, oid, database oid)
    where the configuration is complete, else None; a receipt binds only where both of its oids are integers. verification_run says whether any VERIFICATION_ variable is
    set. recorded is the (name, oid) the test runner recorded creating or selecting, or None; connected_oid is the
    connected database's oid.
    """
    if not owned_settings:
        return "%s refused: these are not the owned test route's settings." % purpose
    if not isinstance(connected, str) or connected != configured:
        return "%s refused: the connected database %r is not the configured database %r." % (purpose, connected, configured)
    stage = clock_route.owned_stage(connected)
    if stage not in stages:
        return "%s refused: %r is %s, and only %s may be used." % (
            purpose, connected, ("the owned route's %s database" % stage) if stage else "not a database of the owned route",
            " or ".join("a %s database" % name for name in stages))
    if owner_is_session_role is not True:
        return "%s refused: this session's role does not own the database %r." % (purpose, connected)
    if receipt_configuration == COMPLETE:
        if receipts is None or not any(
                event in CREATION_EVENTS and name == connected and type(created) is int and type(current) is int
                and created == current for event, name, created, current in receipts):
            return "%s refused: this run's receipts do not record creating the database %r." % (purpose, connected)
        return None
    if receipt_configuration == NONE and verification_run is False:
        if recorded is None:
            return ("%s refused: this run carries no receipts, and its test runner recorded no database of its own. "
                    "Nothing binds the database %r to this run." % (purpose, connected))
        if type(connected_oid) is not int or recorded != (connected, connected_oid):
            return ("%s refused: the connected database %r (oid %r) is not the database this run's test runner "
                    "created or selected, which was %r." % (purpose, connected, connected_oid, recorded))
        return None
    if receipt_configuration == PARTIAL:
        return ("%s refused: this run is half-configured for receipts: %s and %s are set together or not at all, "
                "and one is set without the other. A half-configured run is never treated as a rehearsal."
                % (purpose, RECEIPT_ENV, NONCE_ENV))
    if receipt_configuration == NONE:
        return ("%s refused: this is a verification run (a %s variable is set) and it carries no receipts. "
                "A verification run is never treated as a rehearsal." % (purpose, VERIFICATION_PREFIX + "..."))
    return "%s refused: this run's receipt configuration, %r, is not one the owned route knows." % (purpose, receipt_configuration)


def counter_refusal(purpose, held):
    """Why this counter may not be kept, or None. A pure decision over stated facts.

    held maps each name in COUNTER_AS_MIGRATED to what the database holds. Every one is compared.
    """
    for attribute, migrated in COUNTER_AS_MIGRATED:
        if attribute not in held:
            return "%s refused: the counter's %s was not read. Nothing changed." % (purpose, attribute)
        found = held[attribute]
        if type(found) is not type(migrated) or found != migrated:
            if attribute.startswith("value read"):
                return ("%s refused: the clock is at %r microseconds, not zero. "
                        "A clock that has been moved is never carried through a reversal or an installation. Nothing changed."
                        % (purpose, found))
            return "%s refused: the counter's %s is %r, and the moving part's migration writes %r. Nothing changed." % (
                purpose, attribute, found, migrated)
    return None


def counter_facts(cursor):
    """What the database holds for the counter, by the names in COUNTER_AS_MIGRATED. None where there is no counter."""
    cursor.execute(
        """SELECT pg_catalog.format_type(s.seqtypid, NULL), s.seqstart, s.seqincrement, s.seqmin, s.seqmax, s.seqcache,
                  s.seqcycle, c.relowner = d.datdba,
                  EXISTS (SELECT 1 FROM pg_catalog.pg_depend dep
                           WHERE dep.classid = 'pg_catalog.pg_class'::regclass AND dep.objid = c.oid
                             AND dep.deptype IN ('a', 'i'))
             FROM pg_catalog.pg_sequence s
             JOIN pg_catalog.pg_class c ON c.oid = s.seqrelid
             JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
             JOIN pg_catalog.pg_database d ON d.datname = pg_catalog.current_database()
            WHERE n.nspname = 'public' AND c.relname = 'intevia_test_clock_offset_us'""")
    row = cursor.fetchone()
    if row is None:
        return None
    kind, start, increment, minimum, maximum, cache, cycle, database_owner, column = row
    cursor.execute("SELECT public.intevia_test_clock_offset()")
    owner = "the database's owner" if database_owner is True else "a role that does not own the database"
    return {
        "type": kind, "start": start, "increment": increment, "minimum": minimum, "maximum": maximum, "cache": cache,
        "cycle": cycle, "ownership": "%s, and %s" % (owner, "a table column" if column else "no table column"),
        "value read by intevia_test_clock_offset()": cursor.fetchone()[0],
    }


def require_counter_as_migrated(cursor, purpose):
    """Refuse unless the counter is present, at zero and exactly as the moving part's migration writes it."""
    held = counter_facts(cursor)
    if held is None:
        raise ClockRouteRefused("%s refused: the clock's counter is not present. Nothing changed." % purpose)
    reason = counter_refusal(purpose, held)
    if reason:
        raise ClockRouteRefused(reason)


def _facts(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT pg_catalog.current_database()::text, d.oid::bigint,
                      EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.oid = d.datdba AND r.rolname = session_user)
                 FROM pg_catalog.pg_database d WHERE d.datname = pg_catalog.current_database()""")
        connected, oid, owner = cursor.fetchone()
    receipts = None
    path, nonce = os.environ.get(RECEIPT_ENV), os.environ.get(NONCE_ENV)
    configuration = COMPLETE if path and nonce else PARTIAL if path or nonce else NONE
    if configuration == COMPLETE:
        from verification.ownership import read_receipts

        records, problem = read_receipts(path, nonce)
        receipts = [] if problem else [(r.get("event"), r.get("name"), r.get("oid"), oid) for r in records]
    return {
        "owned_settings": getattr(settings, "S015_TEST_ROUTE", None) == S015_TEST_ROUTE
        and getattr(settings, "INTEVIA_TEST_CONTEXT", False) is True,
        "configured": connection.settings_dict.get("NAME"),
        "connected": connected,
        "owner_is_session_role": owner,
        "receipts": receipts,
        "receipt_configuration": configuration,
        "verification_run": any(name.startswith(VERIFICATION_PREFIX) for name in os.environ),
        "recorded": creation.recorded(connection),
        "connected_oid": oid,
    }


def authorise_install(connection):
    """Refuse unless this connection may install the moving part. Called by the test-only app's migration."""
    reason = refusal("Installing the INTEVIA Clock's moving part", INSTALL_STAGES, **_facts(connection))
    if reason:
        raise ClockRouteRefused(reason)


def _require_lifecycle_backend(connection, purpose):
    """Refuse unless this connection is the lifecycle backend, connected to its qualified active clone."""
    qualify = getattr(connection.ops, "_qualified_database_name", None)
    if qualify is None:
        raise ClockRouteRefused("%s refused: this connection is not the owned test lifecycle backend." % purpose)
    qualify()


def authorise_reverse(connection):
    """Refuse unless this connection may reverse the moving part's migration: the qualified active clone, and nowhere else."""
    purpose = "Reversing the INTEVIA Clock's moving part"
    _require_lifecycle_backend(connection, purpose)
    reason = refusal(purpose, REVERSE_STAGES, **_facts(connection))
    if reason:
        raise ClockRouteRefused(reason)


def authorise_advance(alias="default"):
    """Refuse unless this connection is the qualified active clone, outside every transaction."""
    connection = connections[alias]
    _require_lifecycle_backend(connection, "Moving the INTEVIA Clock")
    reason = refusal("Moving the INTEVIA Clock", ADVANCE_STAGES, **_facts(connection))
    if reason:
        raise ClockRouteRefused(reason)
    for other in connections.all(initialized_only=True):
        if other.in_atomic_block or not other.get_autocommit():
            raise ClockRouteRefused(
                "Moving the INTEVIA Clock refused: the connection %r has a transaction open. "
                "The clock moves only between transactions. Nothing changed." % other.alias)
    return connection


def offset(alias="default"):
    """The clock's offset in elapsed microseconds, read without advancing it."""
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT public.intevia_test_clock_offset()")
        return cursor.fetchone()[0]


def _move(alias, microseconds, target):
    connection = authorise_advance(alias)
    try:
        with connection.cursor() as cursor:
            cursor.execute(MOVE_SQL, [microseconds, target])
            return cursor.fetchone()[0]
    except Error as exc:
        text = str(exc).split("\n")[0]
        if REFUSAL_PREFIX in text:
            raise ClockRouteRefused(text[text.index(REFUSAL_PREFIX):]) from exc
        if getattr(exc.__cause__, "sqlstate", None) == INSIDE_A_TRANSACTION:
            # Storage's own proof spoke: PostgreSQL refused the move's COMMIT. Say so in INTEVIA's words.
            raise ClockRouteRefused(INSIDE_A_TRANSACTION_TEXT) from exc
        raise


def advance(by, alias="default"):
    """Move the clock forward by a duration. Returns the new offset in microseconds."""
    if not isinstance(by, timedelta):
        raise TypeError("the INTEVIA Clock moves by a timedelta")
    microseconds = by // timedelta(microseconds=1)
    if not -LARGEST_OFFSET <= microseconds <= LARGEST_OFFSET:
        # A duration the clock could not hold is refused here, in INTEVIA's words, and storage is not called: a number
        # past storage's own integer range would be refused by the driver instead, in the driver's words. The route's
        # own refusals still come first, as for every move. Within the range, storage alone decides.
        authorise_advance(alias)
        raise ClockRouteRefused(
            "Moving the INTEVIA Clock refused: %s Nothing changed." % (
                "the move would pass the largest offset the clock can hold (%d microseconds, about 285 years)." % LARGEST_OFFSET
                if microseconds > 0 else "the clock moves only forward, and this move is not forward."))
    return _move(alias, microseconds, None)


def advance_to(target, alias="default"):
    """Move the clock forward so that it shows the target time or later. Returns the new offset."""
    # Aware as Django judges it: a zone that gives no offset for this time is not a zone the clock can use.
    if not isinstance(target, datetime) or not timezone.is_aware(target):
        raise TypeError("the INTEVIA Clock moves to an aware datetime")
    return _move(alias, None, target)
