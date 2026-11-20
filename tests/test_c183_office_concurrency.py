"""AC7. A first display in one organisation while another has staged its CARE entry."""
from concurrent.futures import Future
from threading import Event, Thread
from time import monotonic
from unittest.mock import patch

from django.db import DatabaseError, connection, connections
from django.test import TransactionTestCase

from core.models import LivingOrganismEvent
from src.intevia.services.office_record import office_record
from tests.office_record_support import approved_office, observation_footprint, read_source_candidate
from tests.test_law_acts import add_member


class OfficeConcurrencyTests(TransactionTestCase):
    def test_distinct_openers_commit_across_organisations(self):
        self._overlap(shared=False)

    def test_same_opener_across_organisations(self):
        self._overlap(shared=True)

    def _assert_pair(self, owner, page, prior):
        observation = page["care"].get("observation")
        self.assertIsNotNone(observation, "A healthy first display must deliver the committed observation.")
        key = observation["event_ref"]
        details, events, head = observation_footprint(owner)
        self.assertEqual((details, events), ([key], [key]), "Each organisation must hold just its own delivered pair.")
        self.assertNotEqual(head, prior[2], "A new observation must advance this organisation's head.")
        self.assertTrue(LivingOrganismEvent.objects.filter(
            pk=head, living_organism=owner, event_uuid=key, action="RECORD_CARE_OFFICE_OBSERVATION"
        ).exists(), "The organisation head must point to the event that was delivered.")
        return key

    def _overlap(self, *, shared):
        first, first_reader, first_office, *_ = approved_office()
        second, second_reader, second_office, *_ = approved_office()
        if shared:
            # Use the existing two-membership fixture. Only identity creation is
            # substituted; add_member commits the ordinary test JOIN chain.
            with patch("tests.test_sr_office_guards.seed_identity", return_value=first_reader.pk):
                second_reader = add_member(second)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first_reader.pk == second_reader.pk, shared)
        for owner, reader, office in ((first, first_reader, first_office), (second, second_reader, second_office)):
            self.assertTrue(read_source_candidate(owner, reader, office)["may_create"],
                            "Both openers must be able to create a genuine observation.")
        before_a, before_b = observation_footprint(first), observation_footprint(second)
        self.assertEqual((before_a[:2], before_b[:2]), (([], []), ([], [])))
        args_a = dict(credential=first_reader.credential, organism_uuid=first.organism_id,
                      office_definition_uuid=first_office, present_care=True)
        args_b = dict(credential=second_reader.credential, organism_uuid=second.organism_id,
                      office_definition_uuid=second_office, present_care=True)
        preview = office_record(**(args_b | {"present_care": False}))

        with connection.cursor() as cur:
            cur.execute("SELECT pg_backend_pid()")
            pid_b, = cur.fetchone()

        staged, result_a = Future(), Future()
        release_a, waiting, at_actor = Event(), Event(), Event()
        opener_wait = Event()
        lock_refusals = []
        seen_b = []
        second_provisional = []
        insert = "INSERT INTO public.core_careofficeobservation"

        def hold_after_insert(execute, statement, params, many, context):
            result = execute(statement, params, many, context)
            if insert not in str(statement):
                return result
            self.assertTrue(connection.in_atomic_block)
            details, events, head = observation_footprint(first)
            self.assertTrue(len(details) == 1 and details == events and head != before_a[2],
                            "The first session must hold a real, uncommitted pair before admitting the second.")
            with connection.cursor() as observer:
                observer.execute("SELECT pg_backend_pid()")
                pid_a, = observer.fetchone()
                self.assertNotEqual(pid_a, pid_b, "The displays must use different PostgreSQL sessions.")
                staged.set_result(True)
                deadline = monotonic() + 12
                while not release_a.wait(0.025):
                    if monotonic() >= deadline:
                        raise AssertionError("The second display neither finished nor showed a lock wait in time.")
                    observer.execute("SELECT pg_backend_pid()=ANY(pg_blocking_pids(%s))", [pid_b])
                    if observer.fetchone()[0]:
                        waiting.set()
                        if at_actor.is_set():
                            opener_wait.set()
                        break
            # READ COMMITTED: the holder can see B's committed event here
            # even though its own transaction is still open.
            seen_b.append(observation_footprint(second))
            return result

        def first_session():
            try:
                with connection.cursor() as cur:
                    cur.execute("SET statement_timeout = '15s'")
                    cur.execute("SET lock_timeout = '10s'")
                with connection.execute_wrapper(hold_after_insert):
                    page = office_record(**args_a)
                result_a.set_result(page)
            except BaseException as exc:
                if not staged.done():
                    staged.set_exception(exc)
                result_a.set_exception(exc)
            finally:
                connections.close_all()

        def trace_second(execute, statement, params, many, context):
            sql = str(statement)
            actor_query = "FOR NO KEY UPDATE" in sql and "core_identity" in sql and "auth_user" in sql
            if actor_query:
                at_actor.set()
            try:
                result = execute(statement, params, many, context)
                if insert in sql:
                    self.assertTrue(connection.in_atomic_block)
                    second_provisional.append((observation_footprint(first), observation_footprint(second)))
                return result
            except DatabaseError as exc:
                if getattr(exc.__cause__, "sqlstate", None) == "55P03":
                    lock_refusals.append("opener" if actor_query else "insert" if insert in sql else "other")
                raise
            finally:
                if actor_query:
                    at_actor.clear()

        # Limit the main session too, then restore its original settings so
        # later tests in the owned run inherit exactly what they started with.
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
            old_limits = cur.fetchone()
            cur.execute("SELECT set_config('lock_timeout','10s',false), set_config('statement_timeout','15s',false)")
        worker = Thread(target=first_session, daemon=True)
        page_b = error = None
        try:
            worker.start()
            staged.result(timeout=15)
            self.assertEqual(observation_footprint(first), before_a,
                             "The second session must still see the original first organisation head.")
            try:
                with connection.execute_wrapper(trace_second):
                    page_b = office_record(**args_b)
            except DatabaseError as exc:
                if not shared or getattr(exc.__cause__, "sqlstate", None) != "55P03" or not lock_refusals:
                    raise
                error = "55P03"
            self.assertFalse(connection.in_atomic_block, "The second public call must have left its transaction.")
        finally:
            release_a.set()
            try:
                if worker.ident is not None:
                    worker.join(timeout=18)
                    self.assertFalse(worker.is_alive(), "The first session must leave the database and finish.")
            finally:
                with connection.cursor() as cur:
                    cur.execute("SELECT set_config('lock_timeout',%s,false), set_config('statement_timeout',%s,false)",
                                old_limits)

        page_a = result_a.result(timeout=1)
        second_key = page_b["care"].get("observation", {}).get("event_ref") if page_b is not None else None
        if not shared:
            self.assertTrue(not waiting.is_set() and second_key is not None and
                            len(seen_b) == 1 and seen_b[0][:2] == ([second_key], [second_key]),
                            "The second organisation must commit its observation while the first transaction is open.")
        else:
            outcome = "observation committed" if second_key else ("database error 55P03" if error else "facts only")
            location = "shared opener" if opener_wait.is_set() else "another lock" if waiting.is_set() else "no wait seen"
            print(f"AC7 same opener: {location}; second display: {outcome}.")
            if waiting.is_set() and not opener_wait.is_set():
                self.assertEqual(lock_refusals, ["insert"],
                                 "Only opener contention may defer the second organisation's display.")
            if second_key is None:
                self.assertTrue(lock_refusals and set(lock_refusals) <= {"opener", "insert"},
                                "A missing observation requires evidence of a refused opener lock.")
                self.assertEqual(observation_footprint(second), before_b,
                                 "An unsuccessful display must leave the second organisation's history intact.")
                if page_b is not None:
                    self.assertEqual({k: v for k, v in page_b.items() if k != "evaluated_at"},
                                     {k: v for k, v in preview.items() if k != "evaluated_at"},
                                     "Fallback must deliver the permitted facts without CARE's words.")
                page_b = office_record(**args_b)
            elif not waiting.is_set():
                self.assertEqual(seen_b[0][:2], ([second_key], [second_key]),
                                 "An independent second commit must be visible inside the first transaction.")
        key_a = self._assert_pair(first, page_a, before_a)
        key_b = self._assert_pair(second, page_b, before_b)
        self.assertNotEqual(key_a, key_b, "The two organisations must receive separate observation events.")
        if not waiting.is_set() and second_key is not None:
            after_b = observation_footprint(second)
            self.assertEqual(second_provisional, [(before_a, after_b)],
                             "Both writes must overlap before either commits, with the first still invisible to the second.")
            self.assertEqual(seen_b, [after_b], "The second head must be visible before the first commit.")
        replay = office_record(**args_b)
        self.assertEqual(replay["care"].get("observation"), page_b["care"]["observation"],
                         "A later reader of the same office must receive the already committed observation.")
        self.assertEqual(self._assert_pair(second, replay, before_b), key_b)
