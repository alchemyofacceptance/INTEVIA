"""TIMED EVENTS engine runner: one bounded attempt in one Living Organism.

An attempt checks one registered consumer and opens at most one due occurrence,
in the LO the waking request acted in. It decides nothing about what is due: each
consumer's selector reads the domain's own outstanding work, and the landed domain
writer makes the effect. The engine adds a receipt, a pace row and counts.

Rules this module keeps (design D1.4, D1.5, D2; Vision Chamber conditions of 11 Nov):
- One connection. Every function here takes the database alias explicitly. The
  attempt closes and reopens that alias's connection, runs one outer transaction on
  it, and closes it in a ``finally``. The database session id is compared at the
  start and again before commit.
- Never waits. Every lock is a try; a busy lock ends the attempt at once.
- Never raises. ``run`` returns an Attempt whatever happens.
- Nothing silent. Every skip has a class and is counted against the LO where the LO
  is known, including the case where the connection is already inside a transaction.
- No message text. A failure keeps a refusal code its adapter declared, or its
  SQLSTATE class, or a fixed category. Nothing else is stored, returned or logged.
- One rule for pace rows. A pace row is written only under its LO's row lock, taken
  by a try that never waits; a writer that loses the try drops its count.
- The effect and its receipt are written inside one savepoint, so they exist together
  or not at all. Deferred checks and triggers run at the attempt's commit, as they do
  for every landed writer; a failure there undoes the whole attempt, which is then
  counted in a second, small transaction.

This module has no HTTP in it. The request handoff is ``timed_events_handoff``.
"""

import json
import logging
import time
from dataclasses import dataclass
from importlib import import_module

from django.db import Error as DatabaseFault
from django.db import connections, transaction

from core.models import Identity

LOGGER = logging.getLogger("intevia.timed_events")

OPENED = "OPENED"
FOUND_DONE = "FOUND_DONE"
BLOCKED = "BLOCKED"
NOT_DUE = "NOT_DUE"

# Skips charged to no consumer: class -> counter column on core_temeter.
SKIP_COLUMNS = {
    "BUSY_LO": "busy_lo",
    "BUSY_INNER": "busy_inner",
    "IN_TRANSACTION": "in_transaction",
    "NOT_ELIGIBLE": "not_eligible",
    "NO_LIMITS": "no_limits",
    "COMMIT_FAILED": "commit_failed",
    "ERROR": "errors",
}

# The only limits this runner is built to run under (ruling of 11 Nov 2026). They are
# read from the limits version in force; nothing here is used when no version is valid.
SUPPORTED_LIMITS = {
    "consumers_per_request": 1,
    "items_per_request": 1,
    "minimum_probe_gap": None,
    "statement_budget": None,
}

# Modules that register consumer adapters. A part that admits a consumer adds its module.
ADAPTER_MODULES = (
    "src.intevia.services.timed_events_elect",
    "src.intevia.services.timed_events_cq",
    "src.intevia.services.timed_events_cmt",
    "src.intevia.services.timed_events_c2",
)

# Consumers whose landed writers name a human origin of their own as the enterer, never
# the person whose request carried the attempt (ruling P0a-2 A: each landed convention is
# kept). For these, an OPENED receipt takes its enterer from the effect's own proof. For
# every other consumer, and for every FOUND_DONE receipt, the receipt names the carrier.
ENTERER_FROM_PROOF = frozenset({"C2"})

_ADAPTERS = {}
_REFUSALS = {}
_BEFORE_LO = {}

# Fixed safe categories, used when no known code and no SQLSTATE class is available.
UNLISTED = "UNLISTED"  # a domain answer whose code its adapter did not declare
DATABASE = "DB"  # a database error with no SQLSTATE
INTERNAL = "INTERNAL"  # anything that is not a database error


@dataclass(frozen=True)
class Wake:
    """What one confirmed request leaves for the engine: who acted, where, and their epoch."""

    organism_uuid: str
    identity_id: int
    credential_id: int
    access_epoch: int


@dataclass(frozen=True)
class Opening:
    """An adapter's answer for one occurrence.

    outcome is OPENED or FOUND_DONE (effect_ref names the actual effect), BLOCKED
    (code is the domain's own code) or NOT_DUE (the domain no longer owes it).
    """

    outcome: str
    effect_ref: str | None = None
    code: str | None = None


@dataclass(frozen=True)
class Attempt:
    """What one attempt did. result is a skip class, IDLE, or an Opening outcome, or FAILED."""

    result: str
    consumer: str | None = None
    purpose: str | None = None
    source_ref: str | None = None
    failure_class: str | None = None
    code: str | None = None


class _Leave(Exception):
    """Raised inside the savepoint to roll it back without a database error."""

    def __init__(self, opening):
        super().__init__(opening.outcome)
        self.opening = opening


class _SessionChanged(Exception):
    """The database session under the attempt is not the one it started on."""


class _NoProof(Exception):
    """The consumer's validator did not confirm the effect, so no receipt can be written."""


def register(consumer, purpose, adapter, *, refusals=(), before_lo=None):
    """Register the adapter that opens one purpose. Called by a consumer's own module.

    refusals names every domain refusal code the purpose's landed writer is known to
    give. Only those codes are ever kept or returned; any other failure keeps its
    SQLSTATE class or a fixed category, never its text.

    before_lo, if given, tries a lock the landed writer takes before the LO and that
    cannot be derived in SQL (CQ's request key). It must never wait. False means busy.
    """
    _ADAPTERS[(consumer, purpose)] = adapter
    _REFUSALS[(consumer, purpose)] = frozenset(refusals)
    if before_lo is not None:
        _BEFORE_LO[(consumer, purpose)] = before_lo


def _adapter(consumer, purpose):
    for name in ADAPTER_MODULES:
        import_module(name)
    return _ADAPTERS.get((consumer, purpose))


def _known_refusals(consumer, purpose=None):
    """The declared refusal codes of one purpose, or of every purpose of a consumer."""
    for name in ADAPTER_MODULES:
        import_module(name)
    return frozenset().union(*(codes for (owner, name), codes in _REFUSALS.items()
                               if owner == consumer and purpose in (None, name)))


def _rows(alias, sql, params=()):
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, params)
        if not cursor.description:
            return []
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def _session(alias):
    return _rows(alias, "SELECT pg_backend_pid() AS pid")[0]["pid"]


def _inside_transaction(alias):
    """True if this alias's connection is in a transaction the attempt does not own."""
    connection = connections[alias]
    if connection.in_atomic_block:
        return True
    raw = connection.connection
    if raw is None or getattr(raw, "closed", False):
        return False
    if not connection.get_autocommit():
        return True
    from psycopg.pq import TransactionStatus

    return raw.info.transaction_status != TransactionStatus.IDLE


_SKIP_SQL = """
WITH k AS (SELECT lo.id, pg_try_advisory_xact_lock(hashtextextended('TE:meter:'||lo.id::text,0)) AS ok
           FROM public.core_livingorganism lo WHERE lo.organism_id=%s::uuid)
INSERT INTO public.core_temeter (organism_id, {column}, last_skip_class, last_skip_at)
SELECT k.id, 1, %s, statement_timestamp() FROM k WHERE k.ok
ON CONFLICT (organism_id) DO UPDATE SET {column}=public.core_temeter.{column}+1,
    last_skip_class=EXCLUDED.last_skip_class, last_skip_at=EXCLUDED.last_skip_at
"""


def _count_skip(alias, organism_uuid, skip_class, *, separate=False):
    """Count one classified skip against the LO, without waiting. A failure to count is dropped.

    One statement finds the LO and counts. separate=True is used only when this alias's
    own connection is inside a foreign transaction: the statement then runs on one
    short-lived connection of its own, which carries no domain write and no receipt.
    An LO that does not exist has no row to count against, and nothing is written.
    """
    sql = _SKIP_SQL.format(column=SKIP_COLUMNS[skip_class])
    params = [str(organism_uuid), skip_class]
    extra = None
    try:
        if separate:
            extra = connections.create_connection(alias)
            with extra.cursor() as cursor:
                cursor.execute(sql, params)
        else:
            with transaction.atomic(using=alias, durable=True):
                _rows(alias, sql, params)
    except (DatabaseFault, OSError) as exc:  # telemetry never becomes a failure
        LOGGER.warning("timed events skip %s not counted (%s)", skip_class, type(exc).__name__)
    finally:
        if extra is not None:
            try:
                extra.close()
            except (DatabaseFault, OSError) as exc:
                LOGGER.warning("timed events count connection not closed (%s)", type(exc).__name__)


def _count_lost_attempt(alias, organism_uuid, organism_id, chosen, failure_class, code, item):
    """After an attempt that did not commit: count it, and record its probe if that is still true.

    failure_class is COMMIT_FAILED (the commit or the connection failed, which includes
    a deferred check or trigger refusing at commit) or ERROR (something failed before
    the commit, outside any act, for example a selector).

    The loss is always counted against the LO. The consumer's pace row follows the one
    rule of every pace writer: it is written only under the LO's row lock, taken by a
    try that never waits. If the try fails, the pace repair is dropped. If it succeeds,
    the failed probe is recorded, whether or not an item had been selected, so the
    consumer goes to the back of the rotation. The repair writes only if no newer probe
    has been committed since this attempt read the row (chosen["probes"]): a newer
    probe, which may be this attempt's own commit if only its acknowledgement was lost,
    is never overwritten. The cursor moves only to the occurrence this attempt tried.
    """
    _count_skip(alias, organism_uuid, failure_class)
    if chosen is None:
        return
    seen = chosen["probes"]
    cursor = [item[key] if item is not None else chosen[name] for key, name in (
        ("due_at", "cursor_due"), ("purpose", "cursor_purpose"),
        ("source_ref", "cursor_source"), ("occurrence", "cursor_occurrence"))]
    try:
        with transaction.atomic(using=alias, durable=True):
            if not _rows(alias, "SELECT lo.id FROM public.core_livingorganism lo WHERE lo.id=%s "
                                "FOR UPDATE SKIP LOCKED", [organism_id]):
                return
            if seen is None:
                _rows(alias, """INSERT INTO public.core_tepace (organism_id, consumer, last_probe_at, probes, failed,
                        last_failure_class, last_failure_code, last_failure_at,
                        cursor_due, cursor_purpose, cursor_source, cursor_occurrence)
                    VALUES (%s,%s,statement_timestamp(),1,1,%s,%s,statement_timestamp(),%s,%s,%s,%s)
                    ON CONFLICT (organism_id, consumer) DO NOTHING""",
                      [organism_id, chosen["consumer"], failure_class, code, *cursor])
            else:
                _rows(alias, """UPDATE public.core_tepace SET last_probe_at=statement_timestamp(),
                        probes=probes+1, failed=failed+1, last_failure_class=%s, last_failure_code=%s,
                        last_failure_at=statement_timestamp(),
                        cursor_due=%s, cursor_purpose=%s, cursor_source=%s, cursor_occurrence=%s
                    WHERE organism_id=%s AND consumer=%s AND probes=%s""",
                      [failure_class, code, *cursor, organism_id, chosen["consumer"], seen])
    except (DatabaseFault, OSError) as exc:
        LOGGER.warning("timed events lost attempt not marked (%s)", type(exc).__name__)


def _resolve_organism(alias, organism_uuid):
    rows = _rows(alias, "SELECT id FROM public.core_livingorganism WHERE organism_id=%s", [organism_uuid])
    return rows[0]["id"] if rows else None


def _revalidate(alias, wake, organism_id):
    """Try the carrier's rows, then check the carrier again (design D1.5). Returns a skip class or None."""
    carrier = list(
        Identity.objects.using(alias)
        .select_related("credential")
        .select_for_update(no_key=True, of=("self", "credential"), skip_locked=True)
        .filter(pk=wake.identity_id)
    )
    if not carrier:
        exists = Identity.objects.using(alias).filter(pk=wake.identity_id).exists()
        return "BUSY_INNER" if exists else "NOT_ELIGIBLE"
    identity = carrier[0]
    if (
        identity.credential_id != wake.credential_id
        or not identity.credential.is_active
        or identity.access_state != Identity.AccessState.ACTIVE
        or identity.access_epoch != wake.access_epoch
    ):
        return "NOT_ELIGIBLE"
    return None if _active_member(alias, organism_id, identity.pk, None) else "NOT_ELIGIBLE"


def _active_member(alias, organism_id, identity_id, cut):
    """Is this person an ACTIVE member of this LO at the cut (now, if no cut is given)?

    The LO's own state is not asked: an active member of a dormant LO stays eligible.
    """
    state = _rows(alias, "SELECT public.disc_member_state(%s,%s,COALESCE(%s::timestamptz,statement_timestamp())) AS state",
                  [organism_id, identity_id, cut])[0]["state"]
    return state == "ACTIVE"


def _limits(alias):
    """The limits in force, or None. None also when they are not the limits this runner supports."""
    body = _rows(alias, "SELECT public.te_limits_at(statement_timestamp()) AS body")[0]["body"]
    if isinstance(body, str):
        body = json.loads(body)
    return body if body == SUPPORTED_LIMITS else None


def _choose_consumer(alias, organism_id):
    """The consumer probed longest ago in this LO; never-probed first, in registry order."""
    rows = _rows(alias, """SELECT c.consumer, p.probes, p.cursor_due, p.cursor_purpose, p.cursor_source,
            p.cursor_occurrence
        FROM (SELECT r.consumer, min(r.ordinal) AS ordinal FROM public.te_consumers() r GROUP BY r.consumer) c
        LEFT JOIN public.core_tepace p ON p.organism_id=%s AND p.consumer=c.consumer
        ORDER BY p.last_probe_at NULLS FIRST, c.ordinal, c.consumer LIMIT 1""", [organism_id])
    return rows[0] if rows else None


def _registry(alias, consumer):
    """The consumer's purposes, in registry order.

    That order only arranges the selectors inside the one query ``_select`` makes; it
    does not decide which item is taken. ``_select``'s own order does: due time, then
    purpose name, then source, then occurrence. The ordinal decides one thing, in
    ``_choose_consumer``: which consumer is probed first among those never probed.
    """
    return _rows(alias, """SELECT r.purpose, r.rule, r.selector, r.validator, r.claim
        FROM public.te_consumers() r WHERE r.consumer=%s ORDER BY r.ordinal, r.purpose""", [consumer])


def _select(alias, organism_id, registry, known, cursor):
    """At most one due occurrence of this consumer: the first after the cursor, else the first.

    The order is the whole of (due time, purpose, source, occurrence), so equal due
    times are still ordered and a blocked item is passed, not lost. Purposes with equal
    due times are taken in the order of their names, not of their registry ordinals: the
    cursor stores the purpose's name, and this is the order it is compared in.
    """
    quote = connections[alias].ops.quote_name
    parts, params = [], []
    for row in registry:
        parts.append("SELECT %s::text AS purpose, x.source_ref, x.occurrence, x.due_at "
                     "FROM public." + quote(row["selector"]) + "(%s,%s) x")
        params += [row["purpose"], organism_id, known]
    sql = ("SELECT s.purpose, s.source_ref, s.occurrence, s.due_at FROM (" + " UNION ALL ".join(parts) + ") s "
           "ORDER BY ((s.due_at, s.purpose, s.source_ref, s.occurrence) > (%s::timestamptz,%s::text,%s::text,%s::text)) "
           "DESC NULLS LAST, s.due_at, s.purpose, s.source_ref, s.occurrence LIMIT 1")
    params += [cursor["cursor_due"], cursor["cursor_purpose"], cursor["cursor_source"], cursor["cursor_occurrence"]]
    rows = _rows(alias, sql, params)
    return rows[0] if rows else None


def _claim(alias, organism_id, consumer, row, item, phase):
    """Ask the consumer to try the locks its writer will take. False means busy."""
    quote = connections[alias].ops.quote_name
    if phase == "BEFORE_LO":
        _adapter(consumer, item["purpose"])  # make sure the consumer's module has registered
        hook = _BEFORE_LO.get((consumer, item["purpose"]))
        if hook is not None and not hook(alias=alias, organism_id=organism_id, item=item):
            return False
    return bool(_rows(alias, "SELECT public." + quote(row["claim"]) + "(%s,%s,%s,%s,%s) AS held",
                      [organism_id, item["purpose"], item["source_ref"], item["occurrence"], phase])[0]["held"])


def _try_organism(alias, organism_id):
    """Try the LO row without waiting, then register it as the landed writers expect."""
    held = _rows(alias, "SELECT lo.organism_id FROM public.core_livingorganism lo WHERE lo.id=%s "
                        "FOR UPDATE SKIP LOCKED", [organism_id])
    if not held:
        return False
    _rows(alias, "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])", [held[0]["organism_id"]])
    return True


def _probe(alias, organism_id, consumer):
    _rows(alias, """INSERT INTO public.core_tepace (organism_id, consumer, last_probe_at, probes)
        VALUES (%s,%s,statement_timestamp(),1)
        ON CONFLICT (organism_id, consumer) DO UPDATE
        SET last_probe_at=EXCLUDED.last_probe_at, probes=public.core_tepace.probes+1""", [organism_id, consumer])


def _between_effect_and_receipt(opening):
    """Nothing in production. A test replaces it to fail exactly between the effect and its receipt."""


def _write_receipt(alias, wake, organism_id, consumer, row, item, opening):
    quote = connections[alias].ops.quote_name
    if consumer in ENTERER_FROM_PROOF and opening.outcome == OPENED:
        # The effect's own enterer, read from the same proof row that supplies both times.
        written = _rows(alias, """INSERT INTO public.core_tereceipt (organism_id, consumer, purpose, source_ref,
            occurrence, outcome, effect_ref, rule_ref, enterer_id, due_at, effect_at)
        SELECT %s,%s,%s,%s,%s,%s,%s,%s, v.enterer_id, v.due_at, v.effect_at
        FROM public.""" + quote(row["validator"]) + """(%s,%s,%s,%s,%s) v
        RETURNING receipt_uuid""",
                        [organism_id, consumer, item["purpose"], item["source_ref"], item["occurrence"],
                         opening.outcome, opening.effect_ref, row["rule"],
                         organism_id, item["purpose"], item["source_ref"], item["occurrence"], opening.effect_ref])
        if len(written) != 1:
            raise _NoProof()
        return
    written = _rows(alias, """INSERT INTO public.core_tereceipt (organism_id, consumer, purpose, source_ref,
            occurrence, outcome, effect_ref, rule_ref, enterer_id, due_at, effect_at)
        SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s, v.due_at, v.effect_at
        FROM public.""" + quote(row["validator"]) + """(%s,%s,%s,%s,%s) v
        RETURNING receipt_uuid""",
                    [organism_id, consumer, item["purpose"], item["source_ref"], item["occurrence"],
                     opening.outcome, opening.effect_ref, row["rule"], wake.identity_id,
                     organism_id, item["purpose"], item["source_ref"], item["occurrence"], opening.effect_ref])
    if len(written) != 1:
        raise _NoProof()


def _classify(exc, known):
    """A safe class and code for a failure. No message text is ever kept.

    A database error whose message is exactly one of the known refusal codes is that
    domain refusal. Any other database error keeps its two-character SQLSTATE class,
    or DB when it has none. Anything else is INTERNAL.
    """
    if not isinstance(exc, DatabaseFault):
        return "ERROR", INTERNAL
    cause = exc.__cause__ if exc.__cause__ is not None else exc
    diag = getattr(cause, "diag", None)
    primary = getattr(diag, "message_primary", None)
    if isinstance(primary, str) and primary in known:
        return "DOMAIN_REFUSED", primary
    state = getattr(cause, "sqlstate", None) or getattr(diag, "sqlstate", None)
    return "ERROR", (state[:2] if isinstance(state, str) and len(state) == 5 and state.isalnum() else DATABASE)


def _act(alias, wake, organism_id, consumer, row, item, known):
    """The effect and its receipt, together or not at all. Returns (Opening or None, class, code)."""
    adapter = _adapter(consumer, item["purpose"])
    if adapter is None:
        return None, "ERROR", "NO_ADAPTER"
    declared = _known_refusals(consumer, item["purpose"])
    try:
        with transaction.atomic(using=alias):  # a savepoint inside the attempt's transaction
            opening = adapter(alias=alias, organism_id=organism_id, enterer_id=wake.identity_id,
                              purpose=item["purpose"], source_ref=item["source_ref"],
                              occurrence=item["occurrence"], due_at=item["due_at"], known=known)
            if opening.outcome not in (OPENED, FOUND_DONE):
                raise _Leave(opening)
            _between_effect_and_receipt(opening)
            _write_receipt(alias, wake, organism_id, consumer, row, item, opening)
            # Deferred checks and triggers are left to the commit. They cannot be forced
            # here: the landed append function relies on its chain guard staying deferred
            # while the landed deferred triggers write (found by TE-02).
        return opening, None, None
    except _Leave as leave:
        answer = leave.opening
        if answer.code is not None and answer.code not in declared:
            answer = Opening(answer.outcome, code=UNLISTED)  # an undeclared code is not kept
        return answer, None, None
    except _NoProof:
        return None, "ERROR", "NO_PROOF"
    except Exception as exc:  # noqa: BLE001 - any failure of an act stays inside its savepoint and is classified
        failure_class, code = _classify(exc, declared)
        return None, failure_class, code


def _record(alias, organism_id, consumer, item, opening, failure_class, code, started):
    """Outside the savepoint: the result on the pace row, and the cursor moved to this item."""
    outcome = opening.outcome if opening is not None else None
    elapsed = int((time.monotonic() - started) * 1000)
    blocked = outcome == BLOCKED
    failed = opening is None
    _rows(alias, """UPDATE public.core_tepace SET
        opened=opened+%s, found_done=found_done+%s, blocked=blocked+%s, failed=failed+%s,
        last_opened_at=CASE WHEN %s THEN statement_timestamp() ELSE last_opened_at END,
        cursor_due=%s, cursor_purpose=%s, cursor_source=%s, cursor_occurrence=%s,
        last_blocked_source=CASE WHEN %s THEN %s ELSE last_blocked_source END,
        last_blocked_code=CASE WHEN %s THEN %s ELSE last_blocked_code END,
        last_blocked_at=CASE WHEN %s THEN statement_timestamp() ELSE last_blocked_at END,
        last_failure_class=CASE WHEN %s THEN %s ELSE last_failure_class END,
        last_failure_code=CASE WHEN %s THEN %s ELSE last_failure_code END,
        last_failure_at=CASE WHEN %s THEN statement_timestamp() ELSE last_failure_at END,
        last_ms=%s, max_ms=GREATEST(COALESCE(max_ms,0),%s)
        WHERE organism_id=%s AND consumer=%s""",
          [int(outcome == OPENED), int(outcome == FOUND_DONE), int(blocked), int(failed),
           outcome == OPENED,
           item["due_at"], item["purpose"], item["source_ref"], item["occurrence"],
           blocked, item["source_ref"], blocked, opening.code if blocked else None, blocked,
           failed, failure_class, failed, code, failed,
           elapsed, elapsed, organism_id, consumer])


def _attempt(wake, alias):
    connection = connections[alias]
    connection.close()  # the transaction below starts on a new database session
    organism_id = _resolve_organism(alias, wake.organism_uuid)
    if organism_id is None:
        LOGGER.warning("timed events skip NOT_ELIGIBLE with no resolved LO")
        return Attempt("NOT_ELIGIBLE")
    session = _session(alias)
    started = time.monotonic()
    skip = None
    done = Attempt("IDLE")
    chosen = None
    acted = None  # the occurrence an act was tried for, if any
    lost = "ERROR"  # until the attempt reaches its commit
    try:
        with transaction.atomic(using=alias, durable=True):
            skip = _revalidate(alias, wake, organism_id)
            if skip is None and _limits(alias) is None:
                skip = "NO_LIMITS"
            if skip is None:
                chosen = _choose_consumer(alias, organism_id)
            if skip is None and chosen is not None:
                consumer = chosen["consumer"]
                registry = _registry(alias, consumer)
                by_purpose = {row["purpose"]: row for row in registry}
                known = _rows(alias, "SELECT statement_timestamp() AS stamp")[0]["stamp"]
                hint = _select(alias, organism_id, registry, known, chosen)
                if hint is not None and not _claim(
                        alias, organism_id, consumer, by_purpose[hint["purpose"]], hint, "BEFORE_LO"):
                    skip = "BUSY_INNER"
                elif not _try_organism(alias, organism_id):
                    skip = "BUSY_LO"
                else:
                    # Under the LO lock everything read before it is read again at a new known
                    # time: first the person's membership, then the candidate. Both were hints.
                    known = _rows(alias, "SELECT statement_timestamp() AS stamp")[0]["stamp"]
                    member = _active_member(alias, organism_id, wake.identity_id, known)
                    item = _select(alias, organism_id, registry, known, chosen) if member else None
                    same = (item is None and hint is None) or (
                        item is not None and hint is not None
                        and all(item[key] == hint[key] for key in ("purpose", "source_ref", "occurrence")))
                    if not member:
                        skip = "NOT_ELIGIBLE"  # before any pace write or writer call
                    elif item is not None and same and not _claim(
                            alias, organism_id, consumer, by_purpose[item["purpose"]], item, "UNDER_LO"):
                        skip = "BUSY_INNER"
                    else:
                        _probe(alias, organism_id, consumer)
                        done = Attempt("IDLE", consumer=consumer)
                        if item is not None and same:
                            row = by_purpose[item["purpose"]]
                            acted = item
                            opening, failure_class, code = _act(alias, wake, organism_id, consumer, row, item, known)
                            _record(alias, organism_id, consumer, item, opening, failure_class, code, started)
                            done = Attempt(
                                opening.outcome if opening is not None else "FAILED",
                                consumer=consumer, purpose=item["purpose"], source_ref=item["source_ref"],
                                failure_class=failure_class,
                                code=code if opening is None else opening.code)
            if skip is not None:
                transaction.set_rollback(True, using=alias)  # leave nothing and release every try
            else:
                lost = "COMMIT_FAILED"
                if _session(alias) != session:
                    raise _SessionChanged()
    except Exception as exc:  # noqa: BLE001 - whatever stopped the attempt, it is counted and nothing is raised
        # The outer transaction did not commit: nothing of this attempt exists.
        LOGGER.warning("timed events attempt not committed: %s (%s)", lost, type(exc).__name__)
        consumer = chosen["consumer"] if chosen else None
        code = _classify(exc, _known_refusals(consumer) if consumer else frozenset())[1]
        _count_lost_attempt(alias, wake.organism_uuid, organism_id, chosen, lost, code, acted)
        return Attempt(lost, consumer=consumer, purpose=acted["purpose"] if acted else None,
                       source_ref=acted["source_ref"] if acted else None, failure_class=lost, code=code)
    if skip is not None:
        _count_skip(alias, wake.organism_uuid, skip)
        return Attempt(skip)
    return done


def run(wake, *, alias):
    """Make one attempt for one confirmed request. Never raises; never waits on a lock.

    The caller must be finished with this alias's connection: the attempt closes it,
    reopens it, and closes it again when done.
    """
    try:
        if _inside_transaction(alias):
            # This connection belongs to someone else's transaction. Do nothing on it,
            # and say so in the measures, on one short-lived connection of its own.
            _count_skip(alias, wake.organism_uuid, "IN_TRANSACTION", separate=True)
            return Attempt("IN_TRANSACTION")
        try:
            return _attempt(wake, alias)
        finally:
            try:
                connections[alias].close()
            except (DatabaseFault, OSError) as exc:
                LOGGER.warning("timed events connection not closed (%s)", type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - nothing reaches the server (design D1.4)
        LOGGER.warning("timed events attempt failed (%s)", type(exc).__name__)
        return Attempt("FAILED", failure_class="ERROR", code=INTERNAL)
