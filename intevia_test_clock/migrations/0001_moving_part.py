"""The INTEVIA Clock's moving part. Test databases of the owned route only.

Production's database holds the three faces as plain reads (core migration 0096) and
nothing of this file. Only intevia/test_settings.py names this app, and this migration
refuses any database the owned route is not migrating (the bootstrap database of a
template build, or a clone the route creates and migrates directly).

What it adds:

- a counter of elapsed microseconds, public.intevia_test_clock_offset_us: a sequence
  defined to hold zero. A sequence's value is seen at once by every connection and is
  not put back by a rollback, so a move cannot be undone; its RESTART is transactional,
  so the reset is undone with the database reset it belongs to;
- the three faces re-defined as "the built-in read plus the counter", each with the
  volatility of its production face. Every read takes shared admission to the clock,
  held to the end of its transaction;
- the move, public.intevia_test_clock_advance: a procedure, called with CALL. Forward
  only, on an owned clone only. Its first act is COMMIT, which PostgreSQL itself refuses
  inside any transaction block: so the move stands alone, in a transaction it began
  itself and which has read nothing. That refusal is PostgreSQL's own ("invalid
  transaction termination"); the Python helper tells it in INTEVIA's words
  (intevia/test_postgresql_backend/clock.py). The move then takes exclusive admission, and refuses while
  any other role is connected to the database (it could not see that role's transactions)
  and while any other connection has a transaction open. The offset is read and written
  once under that exclusion. What the move guarantees (D8, the reader form): a move is
  refused while any other transaction is open, as the move can see it, while it cannot
  tell whether another connection of its own role has one open, and while any
  transaction that has read the clock is open; no transaction ever reads two offsets.
  The one accepted residue: a transaction that begins in the instant between the move's
  check and its commit, having read no time, sees the new offset throughout, and its
  transaction-start face can fall a few milliseconds before the jump;
- the decision the move applies to each other session, as a function of stated facts,
  public.intevia_test_clock_obstacle: another role's session, an open transaction, or a
  session whose transaction status could not be read;
- the observation that feeds that decision, public.intevia_test_clock_observed_obstacle:
  every other session attached to the database, read afresh from the activity view. The
  move and the reset both call it: one inspection, not two;
- the reset, public.intevia_test_clock_reset: on an owned clone only, under exclusive
  admission, refused when its own transaction has itself read the clock, refused for
  whatever would refuse a move (D8 covers the reset: no
  transaction reads two offsets across it; inspected at every reset, also one from
  zero), refused while any row remains in the reset's scope, and transactional. It
  takes no argument: the one table its scope leaves out, the migration ledger, is written
  into its body here (RESET_KEPT), so no caller can choose what the check skips;
- a mark on each face, in its comment. Production's startup refusal reads it
  (core/clock_startup.py).

The reverse is permitted only on an owned clone whose clock is at zero. It puts the three
production faces back exactly as core migration 0096 writes them (body, parallel safety
and comment), removes the nine routines of the moving part (eight functions and the
move's procedure), and keeps the counter.
Nothing on any path drops the counter: it is the evidence that the database has held a
clock that can be moved, and production's startup refuses any database that holds it
(core/clock_startup.py refuses anything in schema public named for the test route). A
database that has held the moving part is destroyed, never repaired into service.

The forward keeps a counter it finds. Whether it finds one or creates one, the counter is
then compared with what this migration writes: its type, start, increment, minimum,
maximum, cache, cycle and ownership, and the value read by intevia_test_clock_offset(),
which must be zero. Anything else is refused and nothing is changed.

The largest offset is 9007199254740991 microseconds (about 285 years): the largest
whole number of microseconds that the interval arithmetic used here carries exactly.
"""

import re
from importlib import import_module

from django.db import migrations

from intevia.test_postgresql_backend import clock as owned_route

production = import_module("core.migrations.0096_intevia_clock")

ADMISSION = "1229870149, 1129074507"  # the two halves of the clock's advisory lock key
CEILING = 9007199254740991
MARK = "INTEVIA Clock, TEST ROUTE face (this clock can be moved): %s. Installed by intevia_test_clock migration 0001."
# The tables the reset's emptiness check leaves out: the migration ledger, as the lifecycle backend's own reset does
# (S015_RESET_EXCLUDED_TABLES). Written into the reset's body as a literal array, which holds no NULL.
RESET_KEPT = ("django_migrations",)
if not RESET_KEPT or any(not re.fullmatch(r"[a-z_][a-z0-9_]*", name) for name in RESET_KEPT):
    raise RuntimeError("intevia_test_clock 0001: the reset's exempt set must be plain table names")
RESET_KEPT_SQL = "ARRAY[%s]::text[]" % ", ".join("'%s'" % name for name in RESET_KEPT)


def body(builtin):
    """The whole stored body of a test-route face."""
    return " SELECT pg_catalog.%s() + public.intevia_test_clock_shift() " % builtin


def expected(volatility, builtin, meaning):
    """What the catalogue must hold for a test-route face."""
    return (body(builtin), volatility, "u", False, "sql", production.RESULT, None, MARK % meaning)


COUNTER_SQL = """
CREATE SEQUENCE public.intevia_test_clock_offset_us AS bigint
    INCREMENT BY 1 MINVALUE 0 MAXVALUE %(ceiling)s START WITH 0 NO CYCLE CACHE 1
""" % {"ceiling": CEILING}
COUNTER_PRESENT = "SELECT pg_catalog.to_regclass('public.intevia_test_clock_offset_us') IS NOT NULL"
# The nine routines of the moving part: the move's procedure and eight functions. The reverse drops these and nothing else.
PROCEDURES = (
    "intevia_test_clock_advance(bigint, timestamp with time zone, bigint)",
)
FUNCTIONS = (
    "intevia_test_clock_observed_obstacle()",
    "intevia_test_clock_obstacle(oid, oid, boolean)",
    "intevia_test_clock_reset()",
    "intevia_test_clock_shift()",
    "intevia_test_clock_offset()",
    "intevia_test_clock_require_clone()",
    "intevia_test_clock_refuse(text)",
    "intevia_test_clock_is_clone(text)",
)

SQL = """
CREATE FUNCTION public.intevia_test_clock_is_clone(p_name text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $clock$
 SELECT coalesce(p_name ~ '^test_intevia_living_organism_[a-z0-9_]+$', false)
$clock$;

CREATE FUNCTION public.intevia_test_clock_refuse(p_reason text) RETURNS void
LANGUAGE plpgsql AS $clock$
BEGIN
 RAISE EXCEPTION 'INTEVIA Clock: %% Nothing changed.', p_reason USING ERRCODE = '55000';
END $clock$;

CREATE FUNCTION public.intevia_test_clock_require_clone() RETURNS void
LANGUAGE plpgsql AS $clock$
BEGIN
 IF NOT public.intevia_test_clock_is_clone(pg_catalog.current_database()::text) THEN
  PERFORM public.intevia_test_clock_refuse('this database is not an owned test clone, and the clock is moved and reset only on one.');
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.oid = d.datdba
                 WHERE d.datname = pg_catalog.current_database() AND r.rolname = session_user) THEN
  PERFORM public.intevia_test_clock_refuse('this session''s role does not own this database.');
 END IF;
END $clock$;

CREATE FUNCTION public.intevia_test_clock_offset() RETURNS bigint
LANGUAGE sql STABLE AS $clock$
 SELECT s.last_value FROM public.intevia_test_clock_offset_us s
$clock$;

CREATE FUNCTION public.intevia_test_clock_shift() RETURNS interval
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE AS $clock$
BEGIN
 -- Shared admission, held to the end of this transaction. It waits while a move or a reset holds the clock.
 PERFORM pg_catalog.pg_advisory_xact_lock_shared(%(admission)s);
 RETURN (SELECT s.last_value FROM public.intevia_test_clock_offset_us s) * interval '1 microsecond';
END $clock$;

CREATE FUNCTION public.intevia_test_clock_obstacle(p_mover oid, p_role oid, p_transaction_open boolean) RETURNS text
LANGUAGE sql IMMUTABLE AS $clock$
 -- Why another session stops a move, or NULL. A decision over stated facts: the mover's role, the other session's
 -- role (NULL for a background process, which has none), and whether that session is seen to have a transaction open:
 -- true, false, or NULL where that could not be read. What could not be read refuses, in its own words.
 -- The observation states false for every process that is not a client connection, with a role or without one
 -- (a parallel worker runs under its leader's role). So only a client connection is ever judged by the third fact;
 -- the role rule below is applied to every row, a client connection's or not.
 SELECT CASE
  WHEN p_mover IS NULL THEN 'the mover''s own role could not be read.'
  WHEN p_role IS NOT NULL AND p_role <> p_mover THEN 'another role is connected to this database. The move sees only its own role''s open transactions, so the clock moves only while the database''s owning role is the only role connected.'
  WHEN p_transaction_open THEN 'another connection has a transaction open; the clock moves only between transactions.'
  WHEN p_transaction_open IS NULL THEN 'another connection''s transaction status could not be read: its activity reporting is off, or its state shows no transaction start. The clock moves only while every other connection is seen to be idle.'
 END
$clock$;

CREATE FUNCTION public.intevia_test_clock_observed_obstacle() RETURNS text
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE AS $clock$
DECLARE mover oid; obstacle text;
BEGIN
 -- Why the clock may not change now, as far as the other sessions go, or NULL. The one observation: the move
 -- and the reset both call it, so that what stops one stops the other.
 -- A transaction is shown the activity view as it stood when that transaction first read it. The reset runs
 -- inside its caller's transaction, which may have read the view before a peer opened a transaction; so the
 -- view is read afresh here.
 PERFORM pg_catalog.pg_stat_clear_snapshot();
 SELECT r.oid INTO mover FROM pg_catalog.pg_roles r WHERE r.rolname = session_user;
 -- Every other session attached to this database, another role's first. A role sees which role holds a session,
 -- and sees a session's transaction only where the role is its own; so another role's session refuses the move.
 -- A client connection of this role has a transaction open where the view shows its start, and is idle only where
 -- the view says 'idle' and shows no start. Anything else is not known, and is passed on as not known: a session
 -- with activity reporting off shows 'disabled' and no start whether or not it is inside a transaction, and one
 -- that turned reporting back on inside a transaction shows a state and still no start. A process that is not a
 -- client connection is stated as having no transaction open, whatever its role: it adds no open-transaction
 -- obstacle. The role rule still applies to its row: one that names another role refuses, as every row of another
 -- role does. A parallel worker's leader is a client connection, and is judged by its own row.
 SELECT seen.reason INTO obstacle
   FROM pg_catalog.pg_stat_activity a
  CROSS JOIN LATERAL (SELECT public.intevia_test_clock_obstacle(
           mover, a.usesysid,
           CASE WHEN a.backend_type IS DISTINCT FROM 'client backend' THEN false WHEN a.xact_start IS NOT NULL THEN true WHEN a.state = 'idle' THEN false END) AS reason) seen
  WHERE a.datname = pg_catalog.current_database() AND a.pid <> pg_catalog.pg_backend_pid() AND seen.reason IS NOT NULL
  ORDER BY (a.usesysid IS NOT DISTINCT FROM mover), a.pid
  LIMIT 1;
 RETURN obstacle;
END $clock$;

CREATE PROCEDURE public.intevia_test_clock_advance(p_microseconds bigint, p_target timestamp with time zone, INOUT p_offset bigint DEFAULT NULL)
LANGUAGE plpgsql AS $clock$
DECLARE held bigint; step bigint; obstacle text;
BEGIN
 IF (p_microseconds IS NULL) = (p_target IS NULL) THEN
  PERFORM public.intevia_test_clock_refuse('a move is given either a number of microseconds or a target time, not both and not neither.');
 END IF;
 -- The proof that the move stands alone. PostgreSQL refuses transaction control in a procedure that was called
 -- inside any transaction block, explicit or implicit, with "invalid transaction termination" (SQLSTATE 2D000).
 -- After it, this procedure is in a transaction it began itself. No comparison of the statement's start with the
 -- transaction's start is made: two statements sent as one message share a start, and a lone call whose parts
 -- reach the server as separate messages does not, so that comparison can be wrong both ways.
 COMMIT;
 PERFORM public.intevia_test_clock_require_clone();
 IF NOT pg_catalog.pg_try_advisory_xact_lock(%(admission)s) THEN
  PERFORM public.intevia_test_clock_refuse('a transaction that has read the clock is open on another connection, or another move is under way.');
 END IF;
 obstacle := public.intevia_test_clock_observed_obstacle();
 IF obstacle IS NOT NULL THEN
  PERFORM public.intevia_test_clock_refuse(obstacle);
 END IF;
 SELECT s.last_value INTO STRICT held FROM public.intevia_test_clock_offset_us s;
 IF p_target IS NOT NULL THEN
  step := pg_catalog.ceil(extract(epoch FROM (p_target - (pg_catalog.clock_timestamp() + held * interval '1 microsecond'))) * 1000000)::bigint;
 ELSE
  step := p_microseconds;
 END IF;
 IF step <= 0 THEN
  PERFORM public.intevia_test_clock_refuse('the clock moves only forward, and this move is not forward.');
 END IF;
 IF step > %(ceiling)s - held THEN
  PERFORM public.intevia_test_clock_refuse('the move would pass the largest offset the clock can hold.');
 END IF;
 PERFORM pg_catalog.setval('public.intevia_test_clock_offset_us'::regclass, held + step, true);
 p_offset := held + step;
END $clock$;

CREATE FUNCTION public.intevia_test_clock_reset() RETURNS bigint
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE AS $clock$
DECLARE held bigint; scope record; occupied boolean; obstacle text;
BEGIN
 PERFORM public.intevia_test_clock_require_clone();
 -- D8 for the transaction that resets: it must not itself have read the clock. A face read takes shared admission
 -- for its transaction, and a session's own locks never stop its own exclusive try below; so this is asked first.
 -- What it cannot see is a read whose admission was released by a rollback to a savepoint. The database reset,
 -- which is this function's caller, closes that: it starts from no open transaction and reads no face before this.
 IF EXISTS (SELECT 1 FROM pg_catalog.pg_locks l
             WHERE l.locktype = 'advisory' AND l.pid = pg_catalog.pg_backend_pid() AND l.classid = %(admission_first)s
               AND l.objid = %(admission_second)s AND l.objsubid = 2 AND l.mode = 'ShareLock' AND l.granted) THEN
  PERFORM public.intevia_test_clock_refuse('this transaction has itself read the clock; the clock is not reset inside a transaction that has read it.');
 END IF;
 IF NOT pg_catalog.pg_try_advisory_xact_lock(%(admission)s) THEN
  PERFORM public.intevia_test_clock_refuse('a transaction that has read the clock is open on another connection, or a move is under way; the clock is not reset.');
 END IF;
 -- D8 covers the reset: it changes the offset under the same lock as a move, so what would stop a move stops it.
 -- Without this, a transaction whose read of the clock was rolled back to a savepoint holds no admission, is still
 -- open, and would read a second offset after the reset. Inspected at every reset, also one from zero.
 obstacle := public.intevia_test_clock_observed_obstacle();
 IF obstacle IS NOT NULL THEN
  PERFORM public.intevia_test_clock_refuse('the clock is not reset, for the reason that would stop a move: ' || obstacle);
 END IF;
 FOR scope IN SELECT c.relname::text AS name FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname::text <> ALL (%(kept)s)
               ORDER BY 1 LOOP
  EXECUTE pg_catalog.format('SELECT EXISTS (SELECT 1 FROM public.%%I)', scope.name) INTO occupied;
  IF occupied THEN
   PERFORM public.intevia_test_clock_refuse(pg_catalog.format('the reset''s scope is not empty (%%s holds rows); the clock returns to real time only with an empty database.', scope.name));
  END IF;
 END LOOP;
 SELECT s.last_value INTO STRICT held FROM public.intevia_test_clock_offset_us s;
 -- Transactional: if the reset this belongs to is rolled back, the offset returns with the rows.
 ALTER SEQUENCE public.intevia_test_clock_offset_us RESTART WITH 0;
 RETURN held;
END $clock$;
""" % {"ceiling": CEILING, "admission": ADMISSION, "kept": RESET_KEPT_SQL,
       "admission_first": ADMISSION.split(",")[0].strip(), "admission_second": ADMISSION.split(",")[1].strip()}


def face_sql(name, written, builtin):
    """Replace one face's body, keeping its name, arguments, result type, language and volatility."""
    return (
        "CREATE OR REPLACE FUNCTION public.%s() RETURNS %s LANGUAGE sql %s PARALLEL UNSAFE SECURITY INVOKER "
        "AS $intevia_clock$%s$intevia_clock$" % (name, production.RESULT, written, body(builtin))
    )


def production_face_sql(name, written, builtin):
    """Put one production face back: the body and the parallel safety core migration 0096 writes."""
    return (
        "CREATE OR REPLACE FUNCTION public.%s() RETURNS %s LANGUAGE sql %s PARALLEL SAFE SECURITY INVOKER "
        "AS $intevia_clock$%s$intevia_clock$" % (name, production.RESULT, written, production.body(builtin))
    )


def install(apps, schema_editor):
    """Authorise by the owned route's stage; then add the moving part and re-define the faces, each checked both ways.

    A counter that is already present is kept. Present or created, it is compared with what this migration writes.
    """
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        raise RuntimeError("The INTEVIA Clock's moving part needs PostgreSQL.")
    owned_route.authorise_install(connection)
    with connection.cursor() as cursor:
        for name, written, stored, builtin, meaning in production.FACES:
            if production.installed(cursor, name) != production.expected(stored, builtin, meaning):
                raise RuntimeError("intevia_test_clock 0001 refused: public.%s() is not the production face" % name)
        cursor.execute(COUNTER_PRESENT)
        if cursor.fetchone()[0] is not True:
            cursor.execute(COUNTER_SQL)
        cursor.execute(SQL)
        owned_route.require_counter_as_migrated(cursor, "Installing the INTEVIA Clock's moving part")
        for name, written, stored, builtin, meaning in production.FACES:
            cursor.execute(face_sql(name, written, builtin))
            cursor.execute("COMMENT ON FUNCTION public.%s() IS %%s" % name, [MARK % meaning])
        for name, written, stored, builtin, meaning in production.FACES:
            if production.installed(cursor, name) != expected(stored, builtin, meaning):
                raise RuntimeError("intevia_test_clock 0001 refused: public.%s() was not installed as written" % name)


def uninstall(apps, schema_editor):
    """Reverse on an owned clone at zero: production faces back, the nine routines dropped, the counter kept."""
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        raise RuntimeError("The INTEVIA Clock's moving part needs PostgreSQL.")
    owned_route.authorise_reverse(connection)
    purpose = "Reversing the INTEVIA Clock's moving part"
    with connection.cursor() as cursor:
        for name, written, stored, builtin, meaning in production.FACES:
            if production.installed(cursor, name) != expected(stored, builtin, meaning):
                raise RuntimeError("intevia_test_clock 0001 reverse refused: public.%s() is not the test-route face" % name)
        cursor.execute("SELECT pg_catalog.pg_try_advisory_xact_lock(%s)" % ADMISSION)
        if cursor.fetchone()[0] is not True:
            raise owned_route.ClockRouteRefused(
                "%s refused: a transaction that has read the clock is open on another connection, "
                "or a move is under way. Nothing changed." % purpose)
        # A reversal never carries a moved clock: the counter must be at zero and exactly as migrated.
        owned_route.require_counter_as_migrated(cursor, purpose)
        for name, written, stored, builtin, meaning in production.FACES:
            cursor.execute(production_face_sql(name, written, builtin))
            cursor.execute("COMMENT ON FUNCTION public.%s() IS %%s" % name, [production.comment(meaning)])
        for name, written, stored, builtin, meaning in production.FACES:
            if production.installed(cursor, name) != production.expected(stored, builtin, meaning):
                raise RuntimeError("intevia_test_clock 0001 reverse refused: public.%s() was not put back as production writes it" % name)
        # No CASCADE: anything that has come to depend on the moving part stops this.
        for signature in PROCEDURES:
            cursor.execute("DROP PROCEDURE public.%s" % signature)
        for signature in FUNCTIONS:
            cursor.execute("DROP FUNCTION public.%s" % signature)
        # The counter is kept. Nothing here, and nothing on any other path, drops it.


class Migration(migrations.Migration):
    initial = True
    dependencies = (("core", "0096_intevia_clock"),)
    operations = (migrations.RunPython(install, uninstall),)
