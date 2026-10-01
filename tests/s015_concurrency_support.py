from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, Queue
import threading
import time

from django.db import close_old_connections, connection


ORGANISM_LOCK_RELATIONS = {"s015_transaction_register", "core_livingorganism"}


def backend_pid(connection) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


@dataclass
class Worker:
    thread: threading.Thread
    pids: Queue = field(default_factory=Queue)
    results: Queue = field(default_factory=Queue)
    backend_pid: int | None = None

    def pid(self, timeout: float = 10.0) -> int:
        if self.backend_pid is None:
            self.backend_pid = self.pids.get(timeout=timeout)
        return int(self.backend_pid)

    def result(self, timeout: float = 10.0):
        return self.results.get(timeout=timeout)


def start_worker(fn) -> Worker:
    worker = Worker(thread=None)

    def run():
        close_old_connections()
        try:
            worker.pids.put(backend_pid(connection))
            fn()
            worker.results.put("ok")
        except Exception as exc:
            worker.results.put(exc)
        finally:
            connection.close()

    worker.thread = threading.Thread(target=run, daemon=True)
    worker.thread.start()
    return worker


def cleanup_workers(workers, timeout: float = 30.0) -> None:
    for worker in workers:
        if not worker.thread.is_alive():
            continue
        try:
            pid = int(worker.backend_pid) if worker.backend_pid is not None else worker.pid(timeout=timeout)
        except Empty as exc:
            raise AssertionError(f"worker {worker.thread.name} did not report its backend pid") from exc

        cancellation = Queue()

        def cancel_backend():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_cancel_backend(%s::int)", [int(pid)])
                    cancellation.put(cursor.fetchone()[0])
            except Exception as exc:
                cancellation.put(exc)
            finally:
                connection.close()

        cancel_thread = threading.Thread(target=cancel_backend, daemon=True)
        cancel_thread.start()
        cancel_thread.join(timeout=timeout)
        if cancel_thread.is_alive():
            raise AssertionError(f"canceller for worker {worker.thread.name} did not finish")
        cancel_result = cancellation.get(timeout=timeout)
        if isinstance(cancel_result, Exception):
            raise AssertionError(f"could not cancel worker {worker.thread.name}: {cancel_result!r}")
        worker.thread.join(timeout=timeout)
        if worker.thread.is_alive():
            raise AssertionError(f"worker {worker.thread.name} remained alive after cancellation")


def lock_snapshot(cursor, pid) -> dict:
    pid = int(pid)
    cursor.execute("SELECT pg_stat_clear_snapshot()")
    cursor.execute(
        "SELECT pg_blocking_pids(%s::int), wait_event_type, wait_event "
        "FROM pg_stat_activity WHERE pid = %s::int",
        [pid, pid],
    )
    activity = cursor.fetchone()
    if activity is None:
        return {"blockers": [], "wait": (None, None), "locks": []}
    blockers, wait_event_type, wait_event = activity
    cursor.execute(
        "SELECT locktype, CASE WHEN relation IS NULL THEN NULL ELSE relation::regclass::text END, "
        "mode, granted FROM pg_locks WHERE pid = %s::int AND (NOT granted OR locktype = 'tuple')",
        [pid],
    )
    return {
        "blockers": list(blockers or []),
        "wait": (wait_event_type, wait_event),
        "locks": cursor.fetchall(),
    }


def wait_until_locked(
    cursor,
    waiter_pid,
    blocker_pid,
    *,
    relations=None,
    locktype=None,
    timeout: float = 10.0,
) -> dict:
    waiter_pid = int(waiter_pid)
    blocker_pid = int(blocker_pid)
    deadline = time.monotonic() + timeout
    snapshot = None
    while time.monotonic() < deadline:
        snapshot = lock_snapshot(cursor, waiter_pid)
        blockers_match = blocker_pid in snapshot["blockers"]
        wait_match = snapshot["wait"][0] == "Lock"
        relations_match = relations is None or any(
            locktype_ == "tuple" and relation in relations
            for locktype_, relation, _, _ in snapshot["locks"]
        )
        locktype_match = locktype is None or any(
            locktype_ == locktype and not granted
            for locktype_, _, _, granted in snapshot["locks"]
        )
        if blockers_match and wait_match and relations_match and locktype_match:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"waiter pid {waiter_pid} was not observed waiting on blocker pid {blocker_pid}; "
        f"relations={relations!r}, locktype={locktype!r}; last snapshot={snapshot!r}"
    )


def wait_for_blocker_set(cursor, waiter_pid, allowed_blockers, timeout: float = 10.0) -> dict:
    waiter_pid = int(waiter_pid)
    allowed_blockers = {int(pid) for pid in allowed_blockers}
    deadline = time.monotonic() + timeout
    snapshot = None
    while time.monotonic() < deadline:
        snapshot = lock_snapshot(cursor, waiter_pid)
        blockers = set(snapshot["blockers"])
        if snapshot["wait"][0] == "Lock" and blockers and blockers <= allowed_blockers:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"waiter pid {waiter_pid} was not observed waiting on an allowed blocker; "
        f"allowed={allowed_blockers!r}; last snapshot={snapshot!r}"
    )


def wait_until_blocked(
    observer_cursor,
    waiter_pid: int,
    blocker_pid: int,
    query_fragment: str | None = None,
    timeout: float = 10.0,
) -> None:
    deadline = time.monotonic() + timeout
    last_seen = "no pg_stat_activity sample"
    while time.monotonic() < deadline:
        observer_cursor.execute("SELECT pg_stat_clear_snapshot()")
        observer_cursor.execute(
            "SELECT %s::int = ANY(pg_blocking_pids(pid)), wait_event_type, query "
            "FROM pg_stat_activity WHERE pid = %s::int",
            [int(blocker_pid), int(waiter_pid)],
        )
        sample = observer_cursor.fetchone()
        if sample is None:
            last_seen = f"waiter pid {waiter_pid} is absent from pg_stat_activity"
        else:
            is_blocked, wait_event_type, query = sample
            last_seen = (
                f"blocker_present={is_blocked!r}, wait_event_type={wait_event_type!r}, "
                f"query={query!r}"
            )
            has_query = query_fragment is None or query_fragment.lower() in query.lower()
            if is_blocked and wait_event_type == "Lock" and has_query:
                return
        time.sleep(0.01)
    raise AssertionError(
        f"waiter pid {waiter_pid} was not observed blocked by pid {blocker_pid}; "
        f"query_fragment={query_fragment!r}; last saw {last_seen}"
    )