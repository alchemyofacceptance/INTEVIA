"""Public CARE setting, first-display and Board-office record witnesses."""
import json
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction, connection, connections
from django.test import TransactionTestCase

from core import models
from core.care_identity import CARE_ID
from src.intevia.services import care_assistance as care
from src.intevia.services import circle_commands, cq, law_records, library_laws
from src.intevia.services import organism_membership_service as memberships
from src.intevia.services import organism_role_service as roles
from src.intevia.services.office_record import office_record, publish_office_record
from tests import election_support, test_sr_office_actions
from tests.s015_0021_support import database_now
from tests import test_c1b_departure as departure
from tests import cq_authority_support as boards
from tests import test_c2_recovery as recovery
from tests import test_law_acts as law
from tests.test_discussion_office_links import office_basis, record_office
from tests.office_record_support import (
    a1_adopt_policy, a1_definition, approved_office, footprint, observation_footprint,
    propose_observation, read_source_candidate, submit_pair,
)


def set_choice(owner, actor, enabled, prior=None, key=None):
    return care.set_assistance(credential=actor.credential, organism_uuid=owner.organism_id,
                               enabled=enabled, predecessor=prior, request_uuid=key or uuid4())


def read_choice(owner, actor, office):
    value = care.read_assistance(credential=actor.credential, organism_uuid=owner.organism_id,
                                 office_definition_uuid=office)
    return value["enabled"], value["choice_source"]


def fixture_membership_state(owner, founder, reader, state):
    # Same guarded, test-only chain transition as test_discussion_boards.py:122.
    # The production membership command reserves the suspended and restricted transitions.
    member = models.OrganismMembership.objects.get(living_organism=owner, identity=reader)
    fixture = SimpleNamespace(fx=SimpleNamespace(actor=founder, ids={"basis": owner.founding_authority_basis_id}))
    departure.DepartureTests._state(fixture, member, "core_organismmembership",
                                    "core_organismmembershiptransition", state)


def display_args(owner, reader, office, present=True):
    return dict(credential=reader.credential, organism_uuid=owner.organism_id,
                office_definition_uuid=office, present_care=present)


def overlapping_reads(test, owner, first, second):
    """Start the second public command under the first command's actual registered owner lock."""
    pid_ready = Future()
    entering_lock = Event()
    observed_wait = False
    attempted_writes = []
    waiting = None

    def owner_lock(statement, params):
        return ("public.s015_lock_organisms(" in str(statement) and params
                and str(params[0]) == str(owner.organism_id))

    def trace_waiter(execute, statement, params, many, context):
        if owner_lock(statement, params):
            entering_lock.set()
        if "INSERT INTO public.core_careofficeobservation" in str(statement):
            attempted_writes.append(True)
        return execute(statement, params, many, context)

    def contender():
        try:
            with connection.cursor() as cur:
                cur.execute("SET lock_timeout = '8s'")
                cur.execute("SET statement_timeout = '10s'")
                cur.execute("SELECT pg_backend_pid()")
                pid_ready.set_result(cur.fetchone()[0])
            with connection.execute_wrapper(trace_waiter):
                return second()
        except Exception as exc:
            if not pid_ready.done():
                pid_ready.set_exception(exc)
            raise
        finally:
            connections.close_all()

    def after_lock(execute, statement, params, many, context):
        nonlocal waiting, observed_wait
        result = execute(statement, params, many, context)
        if waiting is None and owner_lock(statement, params):
            waiting = pool.submit(contender)
            pid = pid_ready.result(timeout=5)
            test.assertTrue(entering_lock.wait(5), "The second request must reach the registered organisation lock.")
            deadline = monotonic() + 4
            with connection.cursor() as cur:
                while monotonic() < deadline:
                    cur.execute("SELECT pg_backend_pid()=ANY(pg_blocking_pids(%s))", [pid])
                    if cur.fetchone()[0]:
                        observed_wait = True
                        break
                    if waiting.done():
                        waiting.result()
                        test.fail("The competing request completed before waiting on the owner.")
                    sleep(0.01)
            test.assertTrue(observed_wait, "PostgreSQL must identify the first request as the waiting reader's blocker.")
        return result

    with ThreadPoolExecutor(max_workers=1) as pool:
        with connection.execute_wrapper(after_lock):
            first_result = first()
        test.assertTrue(observed_wait, "The commands must overlap on distinct database sessions.")
        second_result = waiting.result(timeout=12)
    return first_result, second_result, attempted_writes


# The independent public-path catalogue and its actual emitting case groups.
# Every group must belong to a real method; each method compares its observed
# union to these paths exactly. Adding an unused permitted field is a failure.
def expected_owner_labels(owner, *, pin_only=False):
    # Read these independently from the fixture; an exact Topic pin grants no Board or tenure lookup.
    name = models.LOFoundingContentVersion.objects.get(living_organism=owner, version=1).name
    labels = {"organisation_full_name": name, "organisation_short_label": name}
    if not pin_only:
        board = models.Circle.objects.filter(parent_organism=owner, kind="GOVERNANCE").exists()
        labels.update(board_full_name="Board of Trustees" if board else None, board_short_label="Board" if board else None)
    return labels


def _paths(prefix, names):
    return {prefix + name for name in names.split()}


OFFICE_PATH_GROUPS = {
    "base": {"evaluated_at", "tenure[]", "history[]", "provisions[]", "topics[]", "decisions[]"}
        | _paths("office.", "organism_uuid requested_definition requested_version code label current_definition current_version")
        | _paths("office.", "organisation_full_name organisation_short_label board_full_name board_short_label")
        | _paths("care.", "enabled choice_source can_change"),
    "vacant": {"tenure[vacant].state"},
    "ongoing": _paths("tenure[ongoing].", "state starts_at ends_at holder_name"),
    "former": _paths("tenure[former].", "state starts_at ends_at holder_name"),
    "law": _paths("provisions[].", "relationship status binding version adoption_record adopting_body in_force_from recorded_at clause adoption_route"),
    "clause_locator": {"provisions[].locator"},
    "adoption_link": {"provisions[].adoption_result_link"},
    "topic": _paths("topics[].", "title created_at office_definition office_version board_uuid circle_uuid category_uuid thread_link"),
    "decision": _paths("decisions[].", "purpose label body result_uuid outcome effective_at recorded_at result_link reporting_rule_link adopted_binding adopted_version"),
    "chosen": _paths("care.", "switch_receipt switched_by switched_by_name"),
    "live": _paths("care.observation.", "label words event_ref source_version source_locator source_link"),
    "law_history": _paths("history[law_adoption].", "kind at label source_link ilc_link ilc_label"),
    "topic_history": _paths("history[topic].", "kind at label source_link ilc_link ilc_label"),
    "decision_history": _paths("history[decision].", "kind at label source_link ilc_link ilc_label"),
    "setting_history": _paths("history[setting_change].", "kind at label source_link ilc_link ilc_label enabled switch_receipt"),
    "care_history": _paths("history[care_observation].", "kind at label source_link ilc_link ilc_label words event_ref source_version source_locator"),
}
OFFICE_PATH_CASES = {
    "R4-fallback": "base vacant law clause_locator topic law_history topic_history".split(),
    "R4-charter": "base vacant law law_history".split(),
    "R1": "base vacant ongoing former law law_history live care_history clause_locator".split(),
    "R3": "base vacant law adoption_link decision law_history decision_history clause_locator".split(),
    "R4-topics": "base vacant law topic law_history topic_history clause_locator".split(),
    "R4-probation": "base vacant law topic law_history topic_history clause_locator".split(),
    "R4-laws": "base vacant law topic law_history topic_history clause_locator".split(),
    "R5": "base vacant law topic chosen live law_history topic_history setting_history care_history clause_locator".split(),
    "R5-empty": "base vacant law topic chosen law_history topic_history setting_history clause_locator".split(),
    "R4-history": "base vacant law topic law_history topic_history care_history clause_locator".split(),
}


class OfficeRecordTests(TransactionTestCase):
    def test_setting_is_local_and_defaults_on(self):
        """B1. Never chosen, foreign Off and explicit first On retain their distinct local provenance."""
        first, a = law.ordinary_birth(board_seats=1)
        second, b = law.ordinary_birth(board_seats=1)
        first_office = record_office(first, a, office_basis(first, a))
        office = record_office(second, b, office_basis(second, b))
        before = footprint(second)
        foreign_key = uuid4()
        with self.subTest(state="never chosen"):
            self.assertEqual(read_choice(second, b, office), (True, "default"),
                             "A new organisation must see On with a default source.")
            self.assertEqual(footprint(second), before, "Reading the default must not create a choice.")
            default = care.read_assistance(credential=b.credential, organism_uuid=second.organism_id,
                                           office_definition_uuid=office)
            self.assertEqual(default, {"enabled": True, "choice_source": "default", "can_change": True},
                             "A default has no switch receipt or attributed switcher.")
        with self.subTest(state="another organisation chose Off"):
            set_choice(first, a, False, key=foreign_key)
            self.assertEqual(read_choice(first, a, first_office), (False, "chosen"),
                             "The organisation that switched Off must see its own recorded choice.")
            foreign_head = footprint(first)[1][-1]
            with self.assertRaises(care.SettingRefused, msg="An owner cannot extend another owner's head.") as wrong:
                set_choice(second, b, False, prior=foreign_head)
            self.assertEqual(wrong.exception.code, "CARE_SETTING_PREDECESSOR",
                             "A foreign predecessor must fail the owner's own head check.")
            self.assertEqual(read_choice(second, b, office), (True, "default"),
                             "Another organisation's Off choice must never become this one's setting.")
            self.assertEqual(footprint(second), before, "A foreign switch must leave local history intact.")
            foreign_before = footprint(first)
            with self.assertRaises(care.SettingRefused, msg="A foreign setting key must give a replay refusal.") as used:
                set_choice(second, b, False, key=foreign_head)
            self.assertEqual(used.exception.code, "CARE_SETTING_REPLAY",
                             "An existing act in another organisation must use the public replay refusal.")
            self.assertEqual((footprint(first), footprint(second)), (foreign_before, before),
                             "A foreign-key collision must preserve both setting histories and both heads.")
        with self.subTest(state="first deliberate On"):
            key = uuid4()
            set_choice(second, b, True, key=key)
            self.assertEqual(read_choice(second, b, office), (True, "chosen"),
                             "An explicit first On must identify a recorded choice.")
            self.assertEqual(footprint(second)[1:], ([key], [key]),
                             "The deliberate choice must have one local detail and matching event.")
            committed = footprint(second)
            foreign_at_retry = footprint(first)
            with self.assertRaises(care.SettingRefused, msg="A foreign key must refuse before the same-value shortcut.") as used:
                set_choice(second, b, True, prior=key, key=foreign_key)
            self.assertEqual(used.exception.code, "CARE_SETTING_REPLAY",
                             "An existing foreign switch key cannot be answered as already on.")
            self.assertEqual((footprint(first), footprint(second)), (foreign_at_retry, committed),
                             "A used foreign key must leave both committed choices and both heads intact.")
            reader = law.add_member(second)
            chosen = care.read_assistance(credential=reader.credential, organism_uuid=second.organism_id,
                                          office_definition_uuid=office)
            self.assertEqual(chosen, {"enabled": True, "choice_source": "chosen", "can_change": False,
                                      "switch_receipt": key, "switched_by": b.identity_id},
                             "A member may see the recorded office-authorised choice without acquiring switch authority.")
        for label, credential, owner_uuid, office_uuid in (
            ("outsider", a.credential, second.organism_id, office),
            ("foreign office", b.credential, second.organism_id, first_office),
            ("absent office", b.credential, second.organism_id, uuid4()),
        ):
            with self.subTest(read=label):
                with self.assertRaises(care.SettingRefused, msg="An inaccessible office must disclose no CARE setting.") as denied:
                    care.read_assistance(credential=credential, organism_uuid=owner_uuid, office_definition_uuid=office_uuid)
                self.assertEqual(denied.exception.code, "CARE_SETTING_UNAVAILABLE",
                                 "Foreign, missing and inaccessible offices must use the same unavailable response.")

    def test_interim_switch_requires_effective_owner_office(self):
        """B2. The effective local organisation office governs switching with voting On or Off.
        Cases cover the voting-on Board, non-office member, Board-only Coordinator, foreign
        office, stale epoch, actual assignment and separate receipt/execution authority cuts.
        """
        owner, scope, people, circle = boards.ordinary(3, board=True)
        founder, member, board_only = people
        before_vote = uuid4()
        set_choice(owner, founder, True, key=before_vote)
        starting = cq.settings(**scope)
        self.assertFalse(starting["settings"]["enabled"],
                         "The first CARE choice must exercise the voting-off route.")
        cq.change_settings(**scope, predecessor=starting["version"], changes={"enabled": True},
                           request_uuid=uuid4())
        circle_commands.activate(**scope, circle_uuid=circle.circle_uuid, request_uuid=uuid4())
        boards.appoint_board_coordinator(owner, circle, founder, board_only)
        self.assertTrue(cq.settings(**scope)["settings"]["enabled"],
                        "Voting must already be on when the Organisation Coordinator changes CARE.")
        voting = cq.settings(**scope)
        new_days = 4 if voting["settings"]["vote_days"] != 4 else 5
        with self.assertRaises(cq.CQRefused, msg="The voting-settings veto must be live for this Board.") as veto:
            cq.change_settings(**scope, predecessor=voting["version"], changes={"vote_days": new_days},
                               request_uuid=uuid4())
        self.assertEqual(veto.exception.code, "CQ_SETTINGS_VOTE",
                         "Three eligible voters and voting already on must route this settings change to a vote.")
        key = uuid4()
        try:
            switch_receipt = set_choice(owner, founder, False, prior=before_vote, key=key)
        except (care.SettingRefused, cq.CQRefused):
            self.fail("The Organisation Coordinator must receive the committed CARE switch while voting is on.")
        except IntegrityError as exc:
            if getattr(getattr(exc.__cause__, "diag", None), "message_primary", None) != "CQ_SETTINGS_VOTE":
                raise
            self.fail("The voting-settings veto must allow the Organisation Coordinator's CARE switch to commit.")
        self.assertEqual(switch_receipt["act_uuid"], key,
                         "The Organisation Coordinator must receive the committed switch during voting.")
        with connection.cursor() as cur:
            cur.execute("""SELECT a.act_uuid,a.actor_id,a.actor_epoch,a.enterer_id,
                    a.authority_source=public.disc_office(a.organism_id,NULL,a.actor_id,a.received_at),
                    a.authority_source=public.disc_office(a.organism_id,NULL,a.actor_id,a.effective_at),
                    a.office_uuid::text=a.authority_source->>'reference',a.enabled,
                    a.received_at,a.effective_at,a.recorded_at,
                    (e.occurred_at,e.received_at,e.effective_at,e.recorded_at)
                     =(a.received_at,a.received_at,a.effective_at,a.recorded_at)
                FROM public.core_careassistancesettingact a
                JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid WHERE a.act_uuid=%s""", [key])
            self.assertEqual(cur.fetchone(), (key, founder.pk, founder.access_epoch, founder.pk,
                                              True, True, True, False, switch_receipt["received_at"],
                                              switch_receipt["effective_at"], switch_receipt["recorded_at"], True),
                             "The switch during voting must bind its bearer, epoch, actual office and paired receipt times.")
        before = footprint(owner)
        withdrawn = recovery.RecoveryTests()
        withdrawn.setUp()
        foreign, foreign_officer = withdrawn.lo, withdrawn.holder.identity
        for label, actor in (("member", member), ("Board Coordinator alone", board_only),
                             ("other organisation's office", foreign_officer)):
            with self.subTest(role=label):
                with self.assertRaises(care.SettingRefused,
                                       msg="A person without this organisation office must not switch CARE.") as denied:
                    set_choice(owner, actor, False, prior=before_vote, key=key)
                expected = "CARE_SETTING_UNAVAILABLE" if actor == foreign_officer else "CARE_SETTING_AUTHORITY"
                self.assertEqual(denied.exception.code, expected,
                                 "Authority must be checked before exposing any existing switch receipt.")
                with self.assertRaises(care.SettingRefused, msg="A fresh key cannot confer the office.") as fresh:
                    set_choice(owner, actor, True, prior=key)
                self.assertEqual(fresh.exception.code, expected,
                                 "Fresh and replay requests must require the same organisation office.")
                self.assertEqual(footprint(owner), before, "An unauthorised request must not change history.")
        with self.subTest(role="stale epoch at execution"):
            with self.assertRaises(IntegrityError, msg="A stale credential epoch must refuse.") as caught:
                with transaction.atomic():
                    submit_pair(owner, founder, uuid4(), "matched", key, True, founder.access_epoch + 1)
            self.assertEqual(caught.exception.__cause__.diag.message_primary, "CARE_SETTING_AUTHORITY",
                             "Epoch loss must be an authority refusal before any switch is accepted.")
            self.assertEqual(footprint(owner), before, "A stale epoch must leave the setting pair unchanged.")

        with self.subTest(role="office ends between receipt and execution"):
            # Probe the detail guard directly. The public switch also serialises on the actor.
            ready = Future()
            owner_locked = Event()
            saw_wait = Event()

            def waiting_switch():
                try:
                    with transaction.atomic(), connection.cursor() as cur:
                        cur.execute("SET LOCAL lock_timeout = '5s'")
                        cur.execute("SET LOCAL statement_timeout = '7s'")
                        cur.execute("""SELECT pg_backend_pid(),public.intevia_clock_statement(),
                              public.disc_office(%s,NULL,%s,public.intevia_clock_statement()) IS NOT NULL""",
                                    [foreign.pk, foreign_officer.pk])
                        pid, received, held_at_receipt = cur.fetchone()
                        self.assertTrue(held_at_receipt, "The requester must hold the office at the switch receipt.")
                        ready.set_result(pid)
                        self.assertTrue(owner_locked.wait(6), "The departure must acquire the organisation lock.")
                        # Wait on the exact row held by s015_register_organism_lock.
                        cur.execute("SELECT id FROM public.core_livingorganism WHERE id=%s FOR UPDATE", [foreign.pk])
                        cur.execute("SELECT public.disc_office(%s,NULL,%s,public.intevia_clock_statement()) IS NULL",
                                    [foreign.pk, foreign_officer.pk])
                        self.assertTrue(cur.fetchone()[0], "The departure must have committed before switch execution.")
                        after_departure = footprint(foreign)
                        with self.assertRaises(IntegrityError, msg="The pending switch must lose its office authority.") as ended:
                            with transaction.atomic():
                                cur.execute("""INSERT INTO public.core_careassistancesettingact
                                      (act_uuid,organism_id,enabled,actor_id,actor_epoch,received_at)
                                      VALUES (%s,%s,false,%s,%s,%s)""",
                                            [uuid4(), foreign.pk, foreign_officer.pk, foreign_officer.access_epoch, received])
                        self.assertEqual(ended.exception.__cause__.diag.message_primary, "CARE_SETTING_AUTHORITY",
                                         "The earlier receipt must not authorise a switch after the office has ended.")
                        return after_departure
                except Exception as exc:
                    if not ready.done():
                        ready.set_exception(exc)
                    raise
                finally:
                    connections.close_all()

            def observe_departure(execute, statement, params, many, context):
                result = execute(statement, params, many, context)
                if (not owner_locked.is_set() and "public.s015_lock_organisms(" in str(statement)
                        and params and str(params[0]) == str(foreign.organism_id)):
                    owner_locked.set()
                    deadline = monotonic() + 3
                    with connection.cursor() as observer:
                        while monotonic() < deadline:
                            observer.execute("SELECT pg_backend_pid()=ANY(pg_blocking_pids(%s))", [switch_pid])
                            if observer.fetchone()[0]:
                                saw_wait.set()
                                break
                            sleep(0.01)
                    self.assertTrue(saw_wait.is_set(), "PostgreSQL must see the switch blocked by the departure.")
                return result

            with ThreadPoolExecutor(max_workers=1) as pool:
                switch = pool.submit(waiting_switch)
                try:
                    switch_pid = ready.result(timeout=6)
                    with connection.execute_wrapper(observe_departure):
                        withdrawn.depart()  # Owns its top-level transaction on this connection.
                finally:
                    owner_locked.set()
                after_departure = switch.result(timeout=8)
            self.assertTrue(saw_wait.is_set(), "Both sessions must participate in the observed organisation-lock wait.")
            self.assertEqual(footprint(foreign), after_departure,
                             "Refusing the switch must preserve the committed departure and add no CARE history.")

    def test_switch_replay_cannot_fork_lineage(self):
        """B3. Identical retry, changed retry, later replay, no-op, stale same value and direct fork."""
        owner, actor = law.ordinary_birth()
        first, second = uuid4(), uuid4()
        receipt = set_choice(owner, actor, False, key=first)
        off_state = footprint(owner)
        self.assertEqual(set_choice(owner, actor, False, prior=first),
                         {"status": "already off", "source_act_uuid": first},
                         "An already-off answer must cite the current choice without issuing another receipt.")
        with connection.cursor() as cur:
            cur.execute("""SELECT event_uuid FROM public.core_livingorganismevent
                            WHERE living_organism_id=%s AND sequence=1 AND action<>'SET_CARE_ASSISTANCE'""",
                        [owner.pk])
            original_event = cur.fetchone()
        self.assertIsNotNone(original_event, "The collision control must use a genuine non-setting ILC event.")
        with self.assertRaises(care.SettingRefused, msg="An existing non-setting ILC key must refuse.") as used:
            set_choice(owner, actor, False, prior=first, key=original_event[0])
        self.assertEqual(used.exception.code, "CARE_SETTING_REPLAY",
                         "An unrelated ILC key must get the replay refusal even when the requested value is current.")
        self.assertEqual(footprint(owner), off_state, "Neither an already-off answer nor a used ILC key may change history.")
        self.assertEqual(set_choice(owner, actor, False, key=first), receipt,
                         "An identical retry must return the first receipt.")
        with self.assertRaises(care.SettingRefused, msg="Changed content under a committed key must refuse.") as changed:
            set_choice(owner, actor, True, key=first)
        self.assertEqual(changed.exception.code, "CARE_SETTING_REPLAY",
                         "Reusing a committed key with changed content must be a replay mismatch.")
        with transaction.atomic():
            submit_pair(owner, actor, second, "matched", first, True)
        two = footprint(owner)
        with connection.cursor() as cur:
            cur.execute("""SELECT act_uuid,version,predecessor FROM public.core_careassistancesettingact
                           WHERE organism_id=%s ORDER BY version""", [owner.pk])
            self.assertEqual(cur.fetchall(), [(first, 1, None), (second, 2, first)],
                             "The first two acts must form one numbered chain under the owner.")
        self.assertEqual(set_choice(owner, actor, False, key=first), receipt,
                         "A still-authorised retry must find the original receipt even after a successor.")
        unchanged = set_choice(owner, actor, True, prior=second)
        self.assertEqual((unchanged["status"], unchanged["source_act_uuid"]), ("already on", second),
                         "An already-on answer must identify the committed choice that keeps CARE on.")
        self.assertEqual(footprint(owner), two, "Repeating the current value with a fresh key is a no-op.")
        with self.assertRaises(care.SettingRefused, msg="A stale predecessor must refuse before no-op handling.") as stale:
            set_choice(owner, actor, True, prior=first)
        self.assertEqual(stale.exception.code, "CARE_SETTING_PREDECESSOR",
                         "A stale same-value request must expressly identify the predecessor problem.")
        with self.assertRaises(IntegrityError, msg="A second direct successor must refuse.") as fork:
            with transaction.atomic():
                submit_pair(owner, actor, uuid4(), "matched", first, False)
        self.assertEqual(fork.exception.__cause__.diag.message_primary, "CARE_SETTING_PREDECESSOR",
                         "The storage guard must diagnose the already-consumed predecessor.")
        self.assertEqual(footprint(owner), two, "Neither a replay nor a refused fork may create a third pair.")

    def test_first_readers_share_one_committed_observation(self):
        """A3. Two eligible first readers and a retry after a lost response share one committed observation."""
        owner, founder, office, _, _, _ = approved_office()
        reader = law.add_member(owner)
        initial = observation_footprint(owner)
        self.assertEqual(initial[:2], ([], []), "Both readers must start with no committed observation.")
        published = []

        def lose_response(page):
            published.append(page)
            raise ConnectionError("The test consumer lost the committed response.")

        def first():
            with self.assertRaises(ConnectionError, msg="The consumer must lose the first committed response."):
                publish_office_record(**display_args(owner, founder, office), publish=lose_response)
            self.assertEqual(len(published), 1, "Delivery failure must not repeat or replace the first publication.")
            return published[0]

        mine, theirs, _ = overlapping_reads(
            self, owner, first, lambda: office_record(**display_args(owner, reader, office)))
        live = mine["care"].get("observation")
        self.assertIsNotNone(live, "The first reader must be offered the words from a committed observation.")
        self.assertEqual(theirs["care"].get("observation"), live,
                         "The waiting eligible reader must receive the same stored words, event and source.")
        key = live["event_ref"]
        after = observation_footprint(owner)
        self.assertEqual(after[:2], ([key], [key]), "Two overlapping displays must commit exactly one detail/event pair.")
        self.assertNotEqual(initial[2], after[2], "The organisation head must advance for the first observation.")
        retry = office_record(**display_args(owner, founder, office))
        self.assertEqual(retry["care"].get("observation"), live,
                         "A retry after a lost response must recover the originally committed observation.")
        self.assertEqual(observation_footprint(owner), after, "The retry must preserve the first pair and its head.")

    def test_outer_commit_controls_delivery(self):
        """A4. Only outer commit may publish words. A real deferred failure publishes fresh permitted facts instead."""
        owner, reader, office, _, _, _ = approved_office()
        args = display_args(owner, reader, office)
        preview = office_record(**display_args(owner, reader, office, False))
        before = observation_footprint(owner)
        published = []
        staged = []
        fact_transactions = []

        def receive(page):
            published.append((connection.in_atomic_block, page, observation_footprint(owner)))

        def fail_at_commit(execute, statement, params, many, context):
            result = execute(statement, params, many, context)
            if "AS visible_vacancy" in str(statement):
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_current_xact_id()::text")
                    fact_transactions.append(cur.fetchone()[0])
            if "INSERT INTO public.core_careofficeobservation" in str(statement) and not staged:
                staged.append(observation_footprint(owner))
                # This disposable-session constraint succeeds at savepoint release and fails at COMMIT.
                with connection.cursor() as cur:
                    cur.execute("""CREATE TEMP TABLE a5_commit_probe (value integer,
                                 CONSTRAINT a5_defer_publish UNIQUE(value) DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP""")
                    cur.execute("INSERT INTO a5_commit_probe VALUES (1),(1)")
            return result

        with connection.execute_wrapper(fail_at_commit):
            try:
                publish_office_record(**args, publish=receive)
            except IntegrityError as exc:
                if exc.__cause__.diag.constraint_name != "a5_defer_publish":
                    raise
                # Judge any premature delivery at the consumer even if the broken publisher leaked the error.
        self.assertEqual(len(staged), 1, "The rollback control must reach a genuine provisional pair.")
        self.assertTrue(len(staged[0][0]) == 1 and staged[0][0] == staged[0][1],
                        "A matched detail and ILC event must exist inside the transaction before its forced rollback.")
        self.assertFalse(any("observation" in page["care"] for _, page, _ in published),
                         "The response consumer must never receive words from the rolled-back pair.")
        self.assertEqual(len(published), 1, "After rollback the consumer must receive exactly one fresh facts-only page.")
        in_transaction, fallback, seen = published[0]
        self.assertFalse(in_transaction, "Fallback delivery must also follow the outermost database commit.")
        self.assertEqual((seen, observation_footprint(owner)), (before, before),
                         "The failed display must leave the detail, event and organisation head at their prior values.")
        self.assertEqual({k:v for k,v in fallback.items() if k != "evaluated_at"},
                         {k:v for k,v in preview.items() if k != "evaluated_at"},
                         "Fallback must preserve every permitted underlying fact and omit all provisional guidance.")
        self.assertEqual(len(set(fact_transactions)), 2,
                         "The failed display and its fallback must read office facts in two distinct transactions.")
        publish_office_record(**args, publish=receive)
        self.assertEqual(len(published), 2, "A healthy retry must deliver exactly one additional response.")
        in_transaction, healthy, seen = published[1]
        self.assertFalse(in_transaction, "A successful consumer must run outside the committing transaction.")
        live = healthy["care"].get("observation")
        self.assertIsNotNone(live, "A healthy explicit retry must present the newly committed observation.")
        self.assertEqual(seen[:2], ([live["event_ref"]], [live["event_ref"]]),
                         "The published reference must already exist on both sides when its consumer receives it.")

    def test_off_wins_before_first_display(self):
        """R6. Off commits while an eligible first reader waits. The reader sees the winning choice and creates no pair."""
        owner, founder, office, _, _, _ = approved_office()
        reader = law.add_member(owner)
        source = read_source_candidate(owner, reader, office)
        self.assertTrue(source["may_create"], "The reader must qualify to create this observation while CARE is On.")
        preview = office_record(**display_args(owner, reader, office, False))
        self.assertEqual((preview["care"]["enabled"], preview["care"]["choice_source"]), (True, "default"),
                         "The reader must begin with the default On setting.")
        self.assertEqual(observation_footprint(owner)[:2], ([], []),
                         "No observation may exist before the switch and the first presentation request.")
        key = uuid4()
        switch, page, attempted = overlapping_reads(
            self, owner, lambda: set_choice(owner, founder, False, key=key),
            lambda: office_record(**display_args(owner, reader, office)))
        self.assertEqual((switch["act_uuid"], page["care"]["enabled"], page["care"]["choice_source"],
                          page["care"].get("switch_receipt")), (key, False, "chosen", key),
                         "The waiting reader must see the committed Off choice and its exact winning receipt.")
        self.assertNotIn("observation", page["care"], "Off must suppress first-display words for a qualifying reader.")
        self.assertFalse(any(row["kind"] == "care_observation" for row in page["history"]),
                         "Off must suppress observation history as well as live words.")
        self.assertEqual((page["office"], page["tenure"]), (preview["office"], preview["tenure"]),
                         "The Off switch must preserve the independently visible office facts.")
        event_id = models.LivingOrganismEvent.objects.values_list("id", flat=True).get(event_uuid=key)
        self.assertEqual(observation_footprint(owner), ([], [], event_id),
                         "The switch must remain the head with no observation detail or observation ILC event.")
        self.assertEqual(footprint(owner), (event_id, [key], [key]),
                         "The setting history must contain only the winning switch and its matching event.")
        self.assertEqual(attempted, [], "Off must stop the first-display append before it is attempted.")

    def test_first_display_preserves_ruled_attribution(self):
        """A5. CARE writes and enters the observation. The active trustee who opened the office stays private.

        Probation is checked at the SQL qualifier here. R4 covers the public probationary read.
        Check the actual immutable envelope and the other reader's live, historical and source-link projections.
        """
        owner, founder, office, _, version, binding = approved_office(board_seats=2)
        board = models.CircleParticipation.objects.get(
            circle__parent_organism=owner, membership__identity=founder, seat_id="trustee-0"
        ).circle
        presenter = boards.seat_trustee(owner, founder, board, 1)
        suspended, restricted, probationary = [law.add_member(owner) for _ in range(3)]
        readers = (("SUSPENDED", suspended), ("RESTRICTED", restricted))
        for state, reader in (*readers, ("PROBATIONARY", probationary)):
            fixture_membership_state(owner, founder, reader, state)
        basis = models.OrganismRoleDefinitionProvision.objects.get(definition__role_uuid=office,
                                                                   relationship="OFFICE_BASIS")
        care_pk = models.Identity.objects.get(identity_id=CARE_ID).pk
        # A storage-level namesake makes a display-name lookup ambiguous.
        # The public registration route reserves this name.
        with transaction.atomic(), connection.cursor() as cur:
            models.Identity.objects.filter(pk=probationary.pk).update(display_name="CARE")
            cur.execute("UPDATE public.core_identityresolution SET display_name=%s WHERE identity_id=%s",
                        ["CARE", probationary.pk])
            cur.execute("""SELECT i.id FROM public.core_identity i JOIN public.core_identityresolution r
                           ON r.identity_id=i.id WHERE i.display_name=%s AND r.display_name=%s""", ["CARE", "CARE"])
            self.assertEqual({key for key, in cur.fetchall()}, {care_pk, probationary.pk},
                             "The fixture must include CARE and a different person with its name.")
        initial = observation_footprint(owner)
        self.assertEqual(initial[:2], ([], []), "The first-reader controls must start without observation history.")

        def show(reader, present=True):
            return office_record(credential=reader.credential, organism_uuid=owner.organism_id,
                                 office_definition_uuid=office, present_care=present)

        def visible_law(reader):
            items = library_laws.our_laws(credential=reader.credential, organism_uuid=owner.organism_id)["laws"]
            return [item["version"] for item in items if item["binding"] == binding]

        def stored_rows(page):
            return [row for row in page["history"] if row["kind"] == "care_observation"]

        with self.subTest(case="law access alone"):
            self.assertEqual(visible_law(probationary), [version.pk],
                             "The probationary control must actually be allowed to read the exact Policy.")
            with connection.cursor() as cur:
                cur.execute("""SELECT public.disc_member_state(%s,%s,public.intevia_clock_statement()),
                               EXISTS (SELECT FROM public.care_alpha_treasurer_qualify(
                                 %s,%s,%s,%s,public.intevia_clock_statement(),false))""",
                            [owner.pk, probationary.pk, owner.organism_id, office,
                             probationary.pk, probationary.access_epoch])
                self.assertEqual(cur.fetchone(), ("PROBATIONARY", False),
                                 "A reader who can read the law but lacks tenure access must get no eligible source.")

        raw_source = read_source_candidate(owner, presenter, office)
        for state, reader in readers:
            with self.subTest(case="first display", membership=state):
                with self.assertRaises(IntegrityError,
                                       msg="Read-only standing must also refuse a direct first insert.") as refused:
                    with transaction.atomic():
                        propose_observation(owner, reader, raw_source, uuid4())
                self.assertEqual(refused.exception.__cause__.diag.message_primary, "CARE_OBSERVATION_SOURCE",
                                 "The database must independently require ACTIVE standing for creation.")
                page = show(reader)
                self.assertTrue(any(row["state"].lower() == "vacant" for row in page["tenure"]),
                                "The reader must actually see the vacant Treasurer tenure.")
                self.assertEqual(visible_law(reader), [version.pk],
                                 "The reader must be able to read the same cited Policy version.")
                self.assertTrue(page["care"]["enabled"], "CARE must be on for the first-reader test.")
                self.assertNotIn("observation", page["care"], "This reader cannot create the first observation.")
                self.assertEqual((stored_rows(page), observation_footprint(owner)), ([], initial),
                                 "A read-only first request must create no history, event or head change.")

        with self.subTest(case="active preview"):
            preview = show(presenter, present=False)
            self.assertNotIn("observation", preview["care"], "An ordinary preview must not create live guidance.")
            self.assertEqual(observation_footprint(owner), initial,
                             "An ACTIVE reader must explicitly request presentation before any pair is written.")

        first = show(presenter)
        with connection.cursor() as cur:
            cur.execute("""SELECT o.requester_id,o.requester_epoch,e.enterer_identity_id,
                                  e.actor_state,e.actor_capacity,e.actor_id,e.actor_access_epoch,
                                  e.entry_mode,e.composing_rule_state,e.composing_rule_reference,o.rule_reference,
                                  e.composer_state,e.composer_identity_id,e.authority_basis_id,
                                  o.definition_uuid,o.provision_uuid,o.binding_uuid,o.library_version_id,
                                  o.qualification_at,o.sentence,to_jsonb(e)::text
                           FROM public.core_careofficeobservation o JOIN public.core_livingorganismevent e
                           ON e.event_uuid=o.event_uuid AND e.living_organism_id=o.organism_id
                           WHERE o.organism_id=%s AND e.action='RECORD_CARE_OFFICE_OBSERVATION'""", [owner.pk])
            stored = cur.fetchone()
        self.assertEqual(stored[2] if stored else None, care_pk,
                         "The first display must commit an observation entered by CARE's fixed identity.")
        live = first["care"].get("observation")
        self.assertIsNotNone(live, "The ACTIVE trustee's explicit request must return committed CARE guidance.")
        paired = observation_footprint(owner)
        self.assertEqual((len(paired[0]), paired[0]), (1, paired[1]),
                         "The first explicit display must commit one detail and one ILC event with the same UUID.")
        event = paired[0][0]
        self.assertNotEqual(paired[2], initial[2], "The first observation must advance the organisation's ILC head.")
        self.assertEqual(stored[:2], (None, None), "The detail must forget the reader and their access epoch.")
        self.assertEqual(stored[3:7], ("NONE", "NA_NO_PARTY", None, None),
                         "Nobody acted or exercised authority by opening the office.")
        self.assertEqual(stored[7:14],
                         ("COMPOSED", "REFERENCE", "CARE:ALPHA:TREASURER_VACANCY:1",
                          "CARE:ALPHA:TREASURER_VACANCY:1", "REFERENCE", care_pk, owner.founding_authority_basis_id),
                         "CARE must compose the words under the cited rule and the organisation's founding authority.")
        self.assertEqual(stored[14:18], (office, basis.provision_uuid, binding, version.pk),
                         "The immutable source must identify this office, exact provision, local binding and version.")
        stamp, words, entry_text = stored[18:]
        for private_value in (str(presenter.identity_id), presenter.canonical_username, presenter.display_name):
            if private_value:
                self.assertNotIn(private_value, entry_text, "The history entry must not identify the trustee who opened the page.")
        live_fields = {"label", "words", "event_ref", "source_version", "source_locator", "source_link"}
        history_fields = {"kind", "at", "label", "source_link", "words", "event_ref", "source_version", "source_locator",
                          "ilc_link", "ilc_label"}

        for label, reader in (("ACTIVE", founder), *readers):
            with self.subTest(case="office-record reuse omits the ILC enterer", membership=label):
                page = show(reader)
                output = page["care"].get("observation")
                self.assertIsNotNone(output, "A reader with both permissions must be able to reuse committed guidance.")
                self.assertEqual(set(output), live_fields, "Live CARE output must expose only its public fields.")
                self.assertEqual(output, live, "The same permitted source must return the same public guidance for each reader.")
                self.assertEqual((output["label"],output["words"],str(output["event_ref"]),output["source_version"],
                                  output["source_locator"],str(output["source_link"])),
                                 ("CARE",words,str(event),version.version_number,"approval",str(basis.provision_uuid)),
                                 "The permitted source link must identify the clause and the event reference must identify the observation.")
                history = stored_rows(page)
                self.assertEqual(len(history), 1, "An authorised later reader must see the stored guidance once in history.")
                row = history[0]
                self.assertEqual(set(row), history_fields,
                                 "The office record's CARE history must omit the ILC enterer's identity, requester epoch and private receipt fields.")
                self.assertEqual({k:row[k] for k in live_fields}, output,
                                 "The historical words, source and event must identify the same public record.")
                self.assertEqual((row["ilc_link"], row["ilc_label"]), (event, "View in ILC"),
                                 "CARE history must link to its own ILC entry without embedding any Enterer identity.")
                first_at = datetime.fromisoformat(row["at"]) if isinstance(row["at"], str) else row["at"]
                self.assertEqual(first_at, stamp, "The historical date must remain the first recorded qualification time.")
                page_text = json.dumps(page, default=str)
                for private_value in (str(presenter.identity_id), presenter.canonical_username, presenter.display_name):
                    if private_value:
                        self.assertNotIn(private_value, page_text,
                                         "The other reader must receive no identifier, name or link identifying the first trustee.")
                self.assertEqual(visible_law(reader), [version.pk],
                                 "The source link must reopen an exact Policy the current reader can see.")
                source = law_records.resolve_citation(owner.organism_id, UUID(str(output["source_link"])))
                self.assertEqual((source["cited"]["cited_version"], source["cited"]["locator"],
                                  source["now"]["governing"]["binding_uuid"]), (version.pk,"approval",binding),
                                 "Opening the scoped provision reference must lead to its exact governing clause.")
                self.assertEqual(observation_footprint(owner), paired,
                                 "Reuse must preserve the committed pair and the organisation head.")

    def test_office_facts_use_effective_tenure(self):
        """R1. Follow an older definition through nomination, appointment, amendment, departure and severance."""
        owner, founder, old, content, _, _ = approved_office()
        reader = law.add_member(owner)
        office_fixture = test_sr_office_actions.SROfficeActionTests()
        office_fixture.actor, office_fixture.organism = founder, owner
        office_fixture.ids = {"basis": owner.founding_authority_basis_id}
        member = office_fixture._member()
        nominee = member.identity
        role = models.OrganismRoleDefinition.objects.get(role_uuid=old)

        def read(definition, present=False):
            page = office_record(**display_args(owner, reader, definition, present))
            self._assert_office_field_paths(page)
            return page

        vacant = read(old)["tenure"]
        self.assertEqual(vacant, [{"state": "vacant"}], "The defined office must be visibly vacant before anyone is nominated.")
        assignment = election_support.office_proposal(owner, founder, nominee, role, self_proposed=False)
        chain = models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment)
        self.assertEqual(list(chain.values_list("action", flat=True)), ["PROPOSE"],
                         "The nomination control must have a genuine proposal and no assignment event.")
        consent = models.OfficeAppointmentConsent.objects.get(assignment=assignment, action="CONSENT")
        pending = read(old)
        self.assertEqual(pending["tenure"], vacant,
                         "A pending nominee must leave an evidenced vacancy with no holder or appointment dates.")

        role_service = roles.OrganismRoleService(authority=boards.authority(), clock=database_now)
        role_service.transition_assignment(office_fixture._transition_command(assignment, "ASSIGN"))
        appointment = chain.get(action="ASSIGN")
        start = appointment.effective_at
        with connection.cursor() as cur:
            cur.execute("SELECT display_name FROM public.core_identityresolution WHERE identity_id=%s", [nominee.pk])
            name, = cur.fetchone()
        self.assertTrue(name, "The holder must have a present display-name resolution for the positive control.")
        ongoing = {"state": "ongoing", "starts_at": start, "ends_at": None, "holder_name": name}
        self.assertEqual(read(old)["tenure"], [ongoing],
                         "The effective assignment must show its own start and the independently resolved holder.")

        current = record_office(owner, founder, content, prior=old)
        current_role = models.OrganismRoleDefinition.objects.get(role_uuid=current)
        self.assertEqual(current_role.supersedes_id, role.pk, "The second definition must actually supersede the first.")
        through_old = read(old, True)
        self.assertEqual(through_old["office"], {**expected_owner_labels(owner), "organism_uuid": owner.organism_id, "code": role.code,
                         "label": "Treasurer", "requested_definition": old, "requested_version": role.definition_version,
                         "current_definition": current, "current_version": current_role.definition_version},
                         "The old UUID must retain the requested version and identify its actual current Board definition.")
        self.assertEqual((through_old["tenure"], read(current)["tenure"]), ([ongoing], [ongoing]),
                         "Both definition UUIDs must show the same continuing appointment under the original version.")
        self.assertNotIn("observation", through_old["care"], "An effective holder must suppress a vacancy observation.")
        for state in ("SUSPENDED", "RESTRICTED"):
            with self.subTest(register_audience=state):
                other_reader = law.add_member(owner)
                fixture_membership_state(owner, founder, other_reader, state)
                permitted = office_record(**display_args(owner, other_reader, old, False))
                self.assertEqual(permitted["tenure"], [ongoing],
                                 "A read-only member in the vacancy-register audience must see the same effective term.")

        departure_service = memberships.OrganismMembershipService(authority=boards.authority(), clock=database_now)
        departure_service.leave_organism(departure.DepartureTests().command(member))
        ending = chain.get(action="END", resulting_state="ENDED")
        end = ending.effective_at
        self.assertEqual(end, models.OrganismMembershipTransition.objects.get(
            membership=member, resulting_state="ENDED").effective_at,
            "The office end must be independently witnessed by the actual membership departure.")
        former = dict(ongoing, state="former", ends_at=end)
        final = read(old, True)
        self.assertEqual(final["tenure"], [{"state": "vacant"}, former],
                         "After departure the vacancy and the former holder must retain the actual effective term dates.")
        live = final["care"].get("observation")
        self.assertIsNotNone(live, "A first display from the old UUID must qualify the vacant current version.")
        current_basis = models.OrganismRoleDefinitionProvision.objects.get(definition=current_role, relationship="OFFICE_BASIS")
        with connection.cursor() as cur:
            cur.execute("""SELECT definition_uuid,provision_uuid FROM public.core_careofficeobservation
                           WHERE organism_id=%s AND event_uuid=%s""", [owner.pk, live["event_ref"]])
            detail = cur.fetchone()
        self.assertEqual((live["source_link"], detail), (current_basis.provision_uuid, (current, current_basis.provision_uuid)),
                         "The displayed source and committed detail must both cite the successor's own provision.")
        self.assertEqual(read(current, True)["care"].get("observation"), live,
                         "A current-version display must reuse the same committed observation as the older link.")

        private_refs = (nominee.identity_id, assignment.assignment_uuid, consent.consent_uuid,
                        appointment.event_uuid, ending.event_uuid)
        visible = json.dumps(final, default=str)
        for ref in private_refs:
            self.assertNotIn(str(ref), visible,
                             "Disclosing the former holder's name and dates must not disclose private person, consent or receipt references.")
        with connection.cursor() as cur:
            cur.execute("DELETE FROM public.core_identityresolution WHERE identity_id=%s", [nominee.pk])
            cur.execute("SELECT EXISTS (SELECT FROM public.core_identityresolutionsevered WHERE identity_id=%s)", [nominee.pk])
            self.assertTrue(cur.fetchone()[0], "The final case must actually sever the former holder's resolution.")
        redacted = read(old)
        self.assertEqual(redacted["tenure"], [{"state": "vacant"}, {"state": "former", "starts_at": start, "ends_at": end}],
                         "Severance must preserve institutional dates and remove the name without a substitute identifier or link.")
        outward = json.dumps(redacted, default=str)
        for private in (name, nominee.canonical_username, *private_refs):
            self.assertNotIn(str(private), outward,
                             "The office record must not disclose a severed name or a private person or receipt reference.")

        self._assert_observed_path_union("R1")

    def test_only_the_approved_treasurer_clause_qualifies(self):
        """A1. Only the pinned Alpha Treasurer clause gives this vacant office live CARE guidance.

        Changed text is the original law for its office, so a loss of IN_FORCE cannot mask a broken match.
        Exact match/reuse, missing and superseded law, alternate route, another office and holder are separate cases.
        """
        from dataclasses import replace
        from hashlib import sha256
        import re

        from core.management.commands import setup_alpha_trustee as alpha
        from src.intevia.services import organism_role_definition_service as definitions

        expected_words = ("The cited policy says the Treasurer approves ordinary payments, "
                          "and this clause does not say who approves them while the office is vacant.")
        exception = " The Chair may approve during a vacancy."
        changed_body = alpha.DUTIES_TEXT + exception
        with self.subTest(case="installed parser and frozen rule"):
            with connection.cursor() as cur:
                cur.execute("""SELECT p.prosrc FROM pg_proc p WHERE p.oid=
                    'public.care_alpha_treasurer_qualify(uuid,uuid,bigint,bigint,timestamptz,boolean)'::regprocedure""")
                source, = cur.fetchone()
                pins = re.findall(r"'([0-9a-f]{64})'::text AS approved_clause_sha256", source)
                self.assertEqual(len(pins), 1, "The installed Alpha rule must expose one frozen approval digest.")
                parsed = []
                for body in (alpha.DUTIES_TEXT, changed_body):
                    cur.execute("SELECT content FROM public.l_clauses(%s) WHERE locator='approval'", [body])
                    rows = cur.fetchall()
                    self.assertEqual(len(rows), 1, "Each governed test document must contain one approval clause.")
                    parsed.append(rows[0][0])
            approved, changed = parsed
            self.assertTrue(approved.startswith(" "), "The LAW parser's retained leading space must be included.")
            self.assertEqual(sha256(approved.encode("utf-8")).hexdigest(), pins[0],
                             "The installed Alpha pin must match the complete parsed A-1c setup clause.")
            self.assertEqual(changed, approved + exception,
                             "The changed clause must retain the complete approved text plus an actual exception.")
            self.assertNotEqual(sha256(changed.encode("utf-8")).hexdigest(), pins[0],
                                "Adding the vacancy exception must break the whole-clause fingerprint.")

        owner, founder = law.ordinary_birth(board_seats=1)
        scope = {"credential": founder.credential, "organism_uuid": owner.organism_id}

        def show(office):
            return office_record(**scope, office_definition_uuid=office, present_care=True)

        def silent(office, message="An ineligible source must leave the live observation absent and the pair and head unchanged."):
            before = observation_footprint(owner)
            page = show(office)
            self.assertTrue(page["care"]["enabled"], "The negative control must exercise CARE while it is on.")
            after = observation_footprint(owner)
            self.assertEqual((page["care"].get("observation"), after), (None, before), msg=message)
            self.assertNotIn("observation", page["care"],
                             "An ineligible source must also omit the empty live observation container.")
            return page

        def resolution(office):
            basis = models.OrganismRoleDefinitionProvision.objects.get(
                definition__living_organism=owner, definition__role_uuid=office, relationship="OFFICE_BASIS")
            return law_records.resolve_citation(owner.organism_id, basis.provision_uuid)

        def bound(office, expected_binding, expected_version):
            view = resolution(office)
            self.assertEqual((view["at_act"]["status"], view["now"]["status"]), ("IN_FORCE", "IN_FORCE"),
                             "Both LAW readings must qualify before the clause content can decide this case.")
            self.assertEqual((view["at_act"]["governing"]["binding_uuid"], view["now"]["governing"]["binding_uuid"],
                              view["cited"]["cited_version"], view["cited"]["locator"]),
                             (expected_binding, expected_binding, expected_version.pk, "approval"),
                             "Both cuts must point to this same local binding, version and approval locator.")

        def prove_vacant(office):
            with connection.cursor() as cur:
                cur.execute("""SELECT NOT EXISTS (
                    SELECT FROM public.core_organismroledefinition d
                    JOIN public.core_organismroledefinition f
                      ON (f.living_organism_id,f.scope,f.kind,f.code)=(d.living_organism_id,d.scope,d.kind,d.code)
                    JOIN public.core_contextualroleassignment a ON a.role_definition_id=f.id
                    WHERE d.role_uuid=%s AND d.living_organism_id=%s AND a.circle_id IS NULL
                      AND public.elect_office_effective(
                        a.id,public.intevia_clock_statement(),public.intevia_clock_statement()))""", [office, owner.pk])
                self.assertTrue(cur.fetchone()[0], "The governed office family must have no effective holder.")

        with self.subTest(case="original in-force clause contains a vacancy exception"):
            resource = "a1-treasurer-duties-" + uuid4().hex
            first_version, first_binding, act = a1_adopt_policy(owner, founder, resource, changed_body)
            self.assertEqual(act.status, "ADOPT", "The longer clause must be adopted before defining the office.")
            role = record_office(owner, founder, a1_definition(resource, first_version.version_number))
            bound(role, first_binding, first_version)
            prove_vacant(role)
            local_law = [v for v in library_laws.our_laws(**scope)["laws"] if v["binding"] == first_binding]
            self.assertEqual([v["content"] for v in local_law], [changed_body],
                             "The authorised founder must be able to read the actual longer Policy in force.")
            silent(role, "The vacancy exception must suppress CARE guidance and leave the detail, ILC event and head unchanged.")

        with self.subTest(case="missing binding"):
            no_link = definitions.DefinitionProvision("OFFICE_BASIS", "library:legacy#approval", "v1")
            role = record_office(owner, founder, definitions.DefinitionContent(
                code=alpha.TREASURER_CODE, provisions=(no_link,)), prior=role)
            unresolved = resolution(role)
            self.assertEqual(unresolved["now"]["status"], "UNRESOLVED",
                             "The unlinked office citation must have no resolvable governing law.")
            silent(role)

        with self.subTest(case="approved complete clause and committed reuse"):
            exact_version, exact_binding, receipt = a1_adopt_policy(
                owner, founder, resource, alpha.DUTIES_TEXT, prior_version=first_version, prior_binding=first_binding)
            self.assertEqual(receipt.status, "ADOPT", "The exact approved clause needs a real successor adoption.")
            definition = a1_definition(resource, exact_version.version_number)
            role = record_office(owner, founder, definition, prior=role)
            bound(role, exact_binding, exact_version)
            prove_vacant(role)
            before = observation_footprint(owner)
            page = show(role)
            observed = page["care"].get("observation")
            self.assertIsNotNone(observed, "A vacant Treasurer with the exact approved clause must get CARE guidance.")
            self.assertEqual((observed["label"], observed["words"], observed["source_version"], observed["source_locator"]),
                             ("CARE", expected_words, exact_version.version_number, "approval"),
                             "The live guidance must show the approved words and exact governing version and locator.")
            basis = models.OrganismRoleDefinitionProvision.objects.get(definition__role_uuid=role, relationship="OFFICE_BASIS")
            self.assertEqual(observed["source_link"], basis.provision_uuid,
                             "The approved words must link to this office's cited approval provision.")
            event = observed["event_ref"]
            paired = observation_footprint(owner)
            self.assertEqual((set(paired[0]) - set(before[0]), set(paired[1]) - set(before[1])), ({event}, {event}),
                             "First display must commit a new detail and a matching ILC event under the displayed reference.")
            self.assertEqual((len(paired[0]), len(paired[1])), (len(before[0]) + 1, len(before[1]) + 1),
                             "The first explicit display must append exactly one observation and one event.")
            event_id = models.LivingOrganismEvent.objects.values_list("id", flat=True).get(living_organism=owner, event_uuid=event)
            self.assertEqual(paired[2], event_id, "The displayed event must be the organisation's committed head.")
            self.assertEqual(show(role)["care"].get("observation"), observed,
                             "Reopening the qualified office must return the original words, source and event.")
            self.assertEqual(observation_footprint(owner), paired,
                             "Reusing a committed observation must leave its detail, event and head unchanged.")

        with self.subTest(case="equivalent text under a successor binding"):
            same_clause = alpha.DUTIES_TEXT + "\n[#review] The Board reviews duties annually."
            latest_version, latest_binding, receipt = a1_adopt_policy(
                owner, founder, resource, same_clause, prior_version=exact_version, prior_binding=exact_binding)
            self.assertEqual(receipt.status, "ADOPT", "A different binding must actually supersede the source.")
            views = resolution(role)
            self.assertEqual((views["at_act"]["status"], views["now"]["status"],
                              views["now"]["governing"]["binding_uuid"]), ("IN_FORCE", "EQUIVALENT", latest_binding),
                             "The same clause under a successor must be distinguishable from the original binding.")
            silent(role)
            definition = a1_definition(resource, latest_version.version_number)

        with self.subTest(case="another Board office citing exactly the same clause"):
            other = record_office(owner, founder, replace(definition, code="SECRETARY"))
            bound(other, latest_binding, latest_version)
            silent(other)

        with self.subTest(case="alternative route with no readable law link"):
            alt = definitions.DefinitionProvision("ALTERNATIVE_ROUTE", "library:legacy#alternative", "v1")
            alternate = replace(definition, alternative_route_reference=alt.provision_reference,
                                provisions=definition.provisions + (alt,))
            role = record_office(owner, founder, alternate, prior=role)
            bound(role, latest_binding, latest_version)
            silent(role)

        with self.subTest(case="confirmed holder across definition versions"):
            role = record_office(owner, founder, definition, prior=role)
            bound(role, latest_binding, latest_version)
            self.assertIsNotNone(show(role)["care"].get("observation"),
                                 "Before appointment, this exact vacant version must qualify for committed guidance.")
            member = law.add_member(owner)
            row = models.OrganismRoleDefinition.objects.get(role_uuid=role)
            assignment = election_support.office_proposal(owner, founder, member, row, self_proposed=False)
            fixture = test_sr_office_actions.SROfficeActionTests()
            command = fixture._transition_command(assignment, "ASSIGN", actor=founder)
            service = roles.OrganismRoleService(authority=boards.authority(), clock=database_now)
            service.transition_assignment(command)
            with connection.cursor() as cur:
                cur.execute("""SELECT public.elect_office_effective(%s,public.intevia_clock_statement(),
                               public.intevia_clock_statement())""", [assignment.pk])
                self.assertTrue(cur.fetchone()[0], "The confirmed Treasurer appointment must be effective now.")
            silent(role)
            newer_role = record_office(owner, founder, definition, prior=role)
            self.assertNotEqual(newer_role, role, "The later definition must have a new version identity.")
            bound(newer_role, latest_binding, latest_version)
            silent(newer_role)

    def _office_audience_case(self, portion):
        """R4, R.2 portion. An office link admits only the parts each underlying source permits."""
        from dataclasses import replace

        from core.management.commands.setup_alpha_trustee import DUTIES_TEXT
        from src.intevia.services import discussion, discussion_posts
        from src.intevia.services.office_record import _ADOPTIONS
        from src.intevia.services.organism_role_definition_service import DefinitionProvision
        from tests import committee_support
        from tests.test_discussion_office_links import discussion_category, public_start_args

        owner, founder, office, definition, first, binding = approved_office(board_seats=0)
        scope = dict(organism_uuid=owner.organism_id, credential=founder.credential)
        independent = connection.copy()
        self.addCleanup(independent.close)

        # Check the read adapter's route names against the checker installed on the binding table.
        with connection.cursor() as cur:
            cur.execute("""SELECT EXISTS (
                SELECT FROM pg_constraint c JOIN pg_depend d ON d.classid='pg_constraint'::regclass AND d.objid=c.oid
                WHERE c.conrelid='public.core_organismlawbinding'::regclass AND c.contype='c' AND c.convalidated
                  AND d.refclassid='pg_proc'::regclass AND d.refobjid='public.l_valid_route(text)'::regprocedure),
                (SELECT bool_and(public.l_valid_route(route)) FROM unnest(%s::text[]) routes(route)),
                public.l_valid_route('UNKNOWN_ADOPTION_ROUTE')""", [sorted(_ADOPTIONS)])
            self.assertEqual(cur.fetchone(), (True, True, False),
                             "Every projected route must satisfy the installed adoption constraint, which must reject the control.")

        def committed():
            with independent.cursor() as cur:
                cur.execute("""SELECT o.head_event_id,
                    ARRAY(SELECT act_uuid FROM public.core_careassistancesettingact WHERE organism_id=o.id ORDER BY version),
                    ARRAY(SELECT event_uuid FROM public.core_careofficeobservation WHERE organism_id=o.id ORDER BY event_uuid),
                    ARRAY(SELECT event_uuid FROM public.core_livingorganismevent WHERE living_organism_id=o.id
                          AND action IN ('SET_CARE_ASSISTANCE','RECORD_CARE_OFFICE_OBSERVATION') ORDER BY sequence)
                    FROM public.core_livingorganism o WHERE o.id=%s""", [owner.pk])
                return cur.fetchone()

        def read(reader, *, target=office, present=False, pin_only=False):
            before = committed()
            earliest = database_now()
            page = office_record(**display_args(owner, reader, target, present))
            self.assertTrue(earliest <= page["evaluated_at"] <= database_now(),
                            "The page must report the database time of this particular read.")
            self.assertEqual(committed(), before, "This office read must preserve both CARE pairs and the committed head.")
            self._assert_office_field_paths(page, pin_only=pin_only)
            return page

        def adoption(version, key, clause, status):
            with connection.cursor() as cur:
                cur.execute("""SELECT b.version_id,b.in_force_from,e.recorded_at,e.event_uuid,b.decision_route
                        FROM public.core_organismlawbinding b JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
                        WHERE b.organism_id=%s AND b.binding_uuid=%s""", [owner.pk, key])
                row = cur.fetchone()
            self.assertIsNotNone(row, "The expected clause must have a recorded adoption in this organisation.")
            self.assertEqual((row[0], row[4]), (version.pk, "COORDINATOR_ROUTE"),
                             "This fixture must use the direct Organisation Coordinator route for the specified version.")
            return dict(relationship="OFFICE_BASIS", locator="approval", clause=clause, version=version.version_number,
                        binding=key, status=status, in_force_from=row[1], recorded_at=row[2], adoption_record=row[3],
                        adopting_body=owner.organism_id, adoption_route="Organisation Coordinator decision")

        original = adoption(first, binding, DUTIES_TEXT.removeprefix("[#approval]"), "cited_and_in_force")
        circle = circle_commands.form(**scope, circle_uuid=uuid4(), kind="GENERIC",
                                      purpose="Discuss the Treasurer office", request_uuid=uuid4())
        board = discussion_category(owner, founder, circle.circle_uuid)
        member = law.add_member(owner)
        committee_support.join(scope, circle, member)
        local = scope | {"circle_uuid": circle.circle_uuid}
        version_number = models.OrganismRoleDefinition.objects.get(role_uuid=office).definition_version

        def opening(title, category):
            args = public_start_args(owner, founder, circle, board) | {"title": title, "category_uuid": category}
            receipt = discussion_posts.start_topic(**args, office_definition_uuid=office)
            event = models.LivingOrganismEvent.objects.get(event_uuid=receipt["act"], living_organism=owner)
            return receipt, dict(title=title, created_at=event.effective_at, office_definition=office,
                                 office_version=version_number, board_uuid=UUID(board["board"]), circle_uuid=circle.circle_uuid,
                                 category_uuid=category, thread_link=receipt["thread"])

        def hide(receipt, topic):
            discussion_posts.set_post_hidden(**local, category_uuid=topic["category_uuid"], thread_uuid=receipt["thread"],
                                             post_uuid=receipt["post"], request_uuid=uuid4(), hidden=True, as_coordinator=True)

        members_category = UUID(board["category"])
        _, sibling = opening("Treasurer duties for members", members_category)
        absent = read(member)
        self.assertEqual((absent["provisions"], absent["topics"]), ([original], [sibling]),
                         "The exact adoption and the readable Topic must both appear before the second opening exists.")
        if portion == "topics":
            receipt, second = opening("A second Treasurer discussion", members_category)
            self.assertEqual(read(member)["topics"], [second, sibling],
                             "Before moderation the ordinary member must see both openings in creation order, newest first.")
            hide(receipt, second)
            hidden = read(member)
            with self.subTest(case="hidden opening and independently readable siblings"):
                self.assertEqual(hidden, absent | {"evaluated_at": hidden["evaluated_at"]},
                                 "A Coordinator-hidden opening must give the same complete member page as its absence, including siblings.")
                self.assertEqual(read(founder)["topics"], [second, sibling],
                                 "The actual board Coordinator must still see the hidden opening, its pin and its sibling.")
                opened = discussion_posts.read_thread(**(local | {"credential": member.credential}), thread_uuid=sibling["thread_link"])
                self.assertEqual((opened["topic"]["title"], opened["topic"]["office_definition_uuid"]), (sibling["title"], office),
                                 "The ordinary reader must be able to reopen their visible Topic and recover its exact office pin.")
                redacted = discussion_posts.read_thread(**(local | {"credential": member.credential}), thread_uuid=second["thread_link"])
                self.assertTrue(redacted["topic"]["title"] is None and "office_definition_uuid" not in redacted["topic"],
                                "The standalone thread must independently withhold the moderated title and its office pin.")

            return

        if portion == "probation":
            positive = office_record(**display_args(owner, founder, office))
            self.assertIn("observation", positive["care"],
                          "The denied-history control needs a genuinely committed observation visible with both permissions.")
            probationer = law.add_member(owner)
            fixture_membership_state(owner, founder, probationer, "PROBATIONARY")
            discussion.set_probation_access(**scope, enabled=True, request_uuid=uuid4())
            discussion.open_circle_welcome(**local, request_uuid=uuid4())
            category = next(c for c in discussion.board_categories(**local)["categories"] if c["functional_code"] == "WELCOME")
            probation_scope = scope | {"credential": probationer.credential}
            laws = library_laws.our_laws(**probation_scope)["laws"]
            self.assertIn((binding, first.content), [(item["binding"], item["content"]) for item in laws],
                          "This probationary reader must be able to open the exact duties law independently.")

            def unavailable(target):
                before = committed()
                with self.assertRaises(care.SettingRefused, msg="Without a readable exact pin this office must be unavailable.") as error:
                    office_record(**display_args(owner, probationer, target, True))
                self.assertEqual(committed(), before, "A refused office read must preserve both CARE pairs and the head.")
                return type(error.exception), error.exception.code, str(error.exception)

            unknown = unavailable(uuid4())
            self.assertEqual(unknown, (care.SettingRefused, "CARE_SETTING_UNAVAILABLE", "This office record is unavailable to you."),
                             "An unknown office must use the single public unavailable response.")
            self.assertEqual(unavailable(office), unknown,
                             "A readable law without a visible office pin must disclose exactly as much as an unknown office.")
            welcome_act, welcome = opening("The Treasurer in Welcome", category["category_uuid"])
            pin_only = read(probationer, present=True, pin_only=True)
            with self.subTest(case="law and Topic pin without tenure"):
                self.assertEqual(pin_only, {"evaluated_at": pin_only["evaluated_at"],
                                 "office": {**expected_owner_labels(owner, pin_only=True),
                                            "organism_uuid": owner.organism_id, "requested_definition": office,
                                            "requested_version": version_number}, "tenure": [], "history": sorted([
                                     {"kind": "law_adoption", "at": original["in_force_from"],
                                      "label": "Office provision adopted", "source_link": binding,
                                      "ilc_link": original["adoption_record"], "ilc_label": "View in ILC"},
                                     {"kind": "topic", "at": welcome["created_at"], "label": "Topic started: " + welcome["title"],
                                      "source_link": welcome["thread_link"], "ilc_link": welcome_act["act"],
                                      "ilc_label": "View in ILC"}],
                                     key=lambda r: (r["at"], str(r["ilc_link"]), r["kind"]), reverse=True),
                                 "care": {"enabled": True, "choice_source": "default", "can_change": False},
                                 "provisions": [original], "topics": [welcome], "decisions": []},
                                 "A visible exact pin and law must survive without tenure, inferred vacancy, guidance or other Topics.")

            hide(welcome_act, welcome)
            self.assertEqual(unavailable(office), unknown,
                             "After hiding the sole readable pin the response must again match an unknown office.")

            return

        alternative = DefinitionProvision("ALTERNATIVE_ROUTE", "library:legacy#alternative", "v1")
        amended = replace(definition, alternative_route_reference=alternative.provision_reference,
                          provisions=definition.provisions + (alternative,))
        latest = record_office(owner, founder, amended, prior=office)
        private = models.OrganismRoleDefinitionProvision.objects.get(definition__role_uuid=latest, relationship="ALTERNATIVE_ROUTE")
        self.assertIsNone(law_records.resolve_citation(owner.organism_id, private.provision_uuid)["cited"]["binding"],
                          "The alternative must actually exist on this definition and lack a verifiable LAW binding.")
        amended_page = read(member)
        with self.subTest(case="unresolved citation beside a readable adoption"):
            self.assertEqual((amended_page["provisions"], amended_page["topics"]), ([original], [sibling]),
                             "An unresolved relationship must add no row, count or placeholder beside readable siblings.")

        revised = " The Treasurer records approvals. The Chair may approve during a vacancy."
        body = "[#approval]" + revised + "\n[#review] This separate clause is read on the law route."
        resource = first.resource.resource_id
        v2, successor, act = a1_adopt_policy(owner, founder, resource, body, prior_version=first, prior_binding=binding)
        self.assertEqual(act.status, "ADOPT", "The changed clause must be in an adopted successor of this exact binding.")
        current = adoption(v2, successor, revised, "in_force_now")
        historical = original | {"status": "cited_at_definition"}
        revised_page = read(member)
        with self.subTest(case="cited and currently governing versions"):
            self.assertEqual((revised_page["provisions"], revised_page["topics"]), ([historical, current], [sibling]),
                             "Keep the originally cited clause, the exact changed clause and adoption, and the surviving Topic.")
            self.assertIn((successor, body), [(row["binding"], row["content"]) for row in library_laws.our_laws(
                          **(scope | {"credential": member.credential}))["laws"]],
                          "The separate law route must still return the whole readable successor, including its other clause.")

        _, _, last = a1_adopt_policy(owner, founder, resource, "[#review] Only the separate clause remains.",
                                     prior_version=v2, prior_binding=successor)
        self.assertEqual(last.status, "ADOPT", "The missing-locator control must have a real in-force successor.")
        final = read(member)
        with self.subTest(case="missing current locator with a surviving historic adoption"):
            self.assertEqual((final["provisions"], final["topics"]), ([historical], [sibling]),
                             "When the current locator is gone, retain its readable historical adoption and Topic alone.")

    def _office_decision_case(self, case):
        """R3. A current Board-adopted Policy identifies a permitted payment result and its separate adoption."""
        from core.management.commands.setup_alpha_trustee import DUTIES_TEXT
        from src.intevia.services import cq_reads, discussion, discussion_posts, law_acts, library_policy
        from tests import cq_settings_support
        from tests.test_cq_clarity import CQClarityTests

        owner, scope, trustees, board = boards.ordinary(3, board=True)
        founder, officer = trustees[:2]
        resource = "r3-office-duties-" + uuid4().hex
        first, binding, receipt = a1_adopt_policy(owner, founder, resource, DUTIES_TEXT)
        self.assertEqual(receipt.status, "ADOPT", "The office fixture needs a recorded initial duties Policy.")
        office = record_office(owner, founder, a1_definition(resource, first.version_number))
        cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": False},
                           request_uuid=uuid4())
        circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())
        boards.appoint_board_coordinator(owner, board, founder, officer)
        local = scope | {"credential": officer.credential, "circle_uuid": board.circle_uuid}
        viewer = law.add_member(owner)
        reader = scope | {"credential": viewer.credential}
        self.topic_category_ids = {}
        peer = connection.copy()
        self.addCleanup(peer.close)

        def footprint():
            with peer.cursor() as cur:
                cur.execute("""SELECT o.head_event_id,
                    ARRAY(SELECT act_uuid FROM public.core_careassistancesettingact WHERE organism_id=o.id ORDER BY version),
                    ARRAY(SELECT event_uuid FROM public.core_careofficeobservation WHERE organism_id=o.id ORDER BY event_uuid),
                    ARRAY(SELECT event_uuid FROM public.core_livingorganismevent WHERE living_organism_id=o.id
                         AND action IN ('SET_CARE_ASSISTANCE','RECORD_CARE_OFFICE_OBSERVATION') ORDER BY sequence)
                    FROM public.core_livingorganism o WHERE o.id=%s""", [owner.pk])
                return cur.fetchone()

        def show(who=viewer):
            before = footprint()
            page = office_record(**display_args(owner, who, office, False))
            self.assertEqual(footprint(), before,
                             "Reading the office decisions must preserve both CARE histories and the committed organisation head.")
            if case == "visible":
                self._assert_office_field_paths(page)
            return page

        def vote(scope, electorate, title, attachment=None, *, choices=None):
            journey, category, topic = CQClarityTests.topic(self, scope, title=title, markdown=title)
            cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=topic["version"], attachment=attachment)
            ballot = cq_settings_support.open_motion(journey, topic["version"], electorate)
            result = cq_settings_support.settle(journey, ballot, electorate, choices)
            self.assertIsNotNone(result, "Full turnout must leave a committed final result for the fixture vote.")
            return journey, category, topic, result

        # The payment has natural wording and no prescribed office marker or effect attachment.
        paid = vote(local, trustees, "Approve the £84 repair to the community room door.")
        self.assertEqual(paid[3]["count"]["kind"], "QUALIFIED",
                         "The positive control needs a carried Board payment.")
        self.assertEqual(show()["decisions"], [], "The initial Policy contains no payment citation or Board adoption.")

        previous_version = first
        previous_binding = binding
        approval = DUTIES_TEXT.removeprefix("[#approval]")
        narrative = "The repair is approved for the community room. The Treasurer keeps the receipt."

        def adopt(marker, *, label):
            nonlocal previous_version, previous_binding
            body = DUTIES_TEXT + "\n[#payment-note] " + narrative
            if marker is not None:
                body += "\n[#payment-result] " + marker
            version = library_policy.draft_policy(**scope, resource_reference=resource, content=body,
                authors=[founder.identity_id], declare_own_authorship=True, request_uuid=uuid4(),
                predecessor_version_id=previous_version.pk)
            publication = {"kind": "PUBLISH_POLICY", "spec": {"resource": resource,
                           "organism": str(owner.organism_id), "version": version.version_number}, "text": version.content}
            published = vote(local, trustees, "Publish " + label, publication)
            self.assertEqual(published[3].get("disposition"), "APPLIED",
                             "The Board publication must actually publish this exact Policy version.")
            proposal = law_acts.LawProposal(organism=owner.organism_id, resource=resource, proposal=uuid4(),
                predecessor=previous_binding, operation="ADOPT", intent="ADOPT", law_class="POLICY",
                version=version.version_number)
            adopted = vote(local, trustees, "Adopt " + label,
                           {"kind": "LAW", "spec": proposal.content(), "text": version.content})
            self.assertEqual(adopted[3].get("disposition"), "APPLIED",
                             "The Board adoption must actually put this exact successor into force.")
            effect = cq_reads.implementation_record(**adopted[0], result_uuid=adopted[3]["post_uuid"])["effect"]
            self.assertIsNotNone(effect, "The adoption must have an implementation distinct from the result.")
            self.assertEqual((effect["domain"], effect["adopted_binding"]), ("LAW", proposal.proposal),
                             "The adoption result must implement this successor's own binding.")
            self.assertNotEqual(published[3]["post_uuid"], adopted[3]["post_uuid"],
                                "Publication and adoption must be separate Board results.")
            previous_version, previous_binding = version, proposal.proposal
            return version, proposal.proposal, published, adopted

        version, current_binding, published, adopted = adopt("  " + str(paid[3]["post_uuid"]) + "  ", label="repair duties")
        self.assertEqual(len({paid[3]["post_uuid"], published[3]["post_uuid"], adopted[3]["post_uuid"]}), 3,
                         "The payment, publication and adoption must have three distinct result receipts.")

        def expected(result, purpose, label, *, binding=None, version=None):
            event = models.LivingOrganismEvent.objects.get(living_organism=owner, event_uuid=result["post_uuid"])
            row = dict(purpose=purpose, label=label, body=board.circle_uuid, result_uuid=result["post_uuid"],
                       outcome="Carried.", effective_at=event.effective_at, recorded_at=event.recorded_at,
                       result_link=result["post_uuid"], reporting_rule_link=result["post_uuid"])
            if binding is not None:
                row.update(adopted_binding=binding, adopted_version=version.version_number)
            return row

        payment = expected(paid[3], "one_payment", "cited by the Policy in force")
        standing = expected(adopted[3], "standing_adoption", "adopted the Policy in force",
                            binding=current_binding, version=version)
        visible = show()
        self.assertEqual(visible["decisions"], [standing, payment],
                         "The office must show the exact cited payment result and its separate adoption, with their own receipts.")
        self.assertEqual([r["clause"] for r in visible["provisions"]], [approval, approval],
                         "Only the office's approval clause belongs in the provision rows, with original and current sources.")
        source = next(row for row in library_laws.our_laws(**reader)["laws"] if row["binding"] == current_binding)
        self.assertEqual(source["content"], version.content,
                         "The member's independent LAW route must preserve the complete readable Policy and its free wording.")
        self.assertEqual(cq_reads.result_post(**(paid[0] | {"credential": viewer.credential}),
                                             result_uuid=payment["result_link"])["post_uuid"], payment["result_uuid"],
                         "The unseated member must be able to reopen the exact payment result shown on the office page.")
        self.assertIsNotNone(cq_reads.reporting_decision(**(paid[0] | {"credential": viewer.credential}),
                                                       result_uuid=payment["reporting_rule_link"]),
                             "The separately shown reporting link must open for the same reader.")
        with self.assertRaises(discussion.DiscussionUnavailable,
                               msg="The Board-result exception must leave the unseated member outside the private thread."):
            discussion_posts.read_thread(**(paid[0] | {"credential": viewer.credential}))

        def hide(vote, hidden):
            journey, category, topic, _ = vote
            discussion_posts.set_post_hidden(**journey, category_uuid=category, post_uuid=topic["post"],
                                             request_uuid=uuid4(), hidden=hidden, as_coordinator=True)

        if case == "visible":
            expected_history = [{"kind": "decision", "at": row["effective_at"], "label": "Board vote carried",
                                 "source_link": row["result_uuid"], "ilc_link": row["result_uuid"],
                                 "ilc_label": "View in ILC"} for row in (standing, payment)]
            self.assertEqual([row for row in visible["history"] if row["kind"] == "decision"],
                             sorted(expected_history, key=lambda r: (r["at"], str(r["ilc_link"]), r["kind"]), reverse=True),
                             "Each readable result must have its own dated Board-vote history row and exact ILC destination.")
            with self.subTest(case="readable Policy with a hidden payment result"):
                hide(paid, True)
                withheld = show()
                self.assertEqual(withheld["decisions"], [standing],
                                 "A hidden payment result must leave the permitted adoption and omit the payment row.")
                self.assertNotIn(str(payment["result_uuid"]), json.dumps(withheld, default=str),
                                 "The withheld payment UUID must be absent from the entire office response.")
                self.assertIn(str(payment["result_uuid"]), next(row for row in library_laws.our_laws(**reader)["laws"]
                                                               if row["binding"] == current_binding)["content"],
                              "The reader must still be able to read the cited UUID as ordinary Policy text on the law route.")
                self.assertEqual(show(officer)["decisions"], [standing, payment],
                                 "The effective Board Coordinator must retain the ordinary hidden-opening override.")
                hide(paid, False)
                self.assertEqual(show()["decisions"], [standing, payment],
                                 "Restoring the payment opening must restore its permitted office link.")

            with self.subTest(case="readable payment with a hidden adoption result"):
                hide(adopted, True)
                withheld = show()
                self.assertEqual(withheld["decisions"], [payment],
                                 "A hidden adoption result must leave the independently readable cited payment.")
                self.assertEqual([r["clause"] for r in withheld["provisions"]], [approval, approval],
                                 "Withholding the adoption vote must preserve the exact readable clauses and adoption fact.")
                self.assertTrue(all("adoption_result_link" not in row for row in withheld["provisions"]),
                                "A restricted adoption vote must not leak through a provision's nested result link.")
                self.assertNotIn(str(standing["result_uuid"]), json.dumps(withheld, default=str),
                                 "The adoption vote UUID must be absent across the whole office projection.")
                hide(adopted, False)

            return

        control = None
        if case == "membership":
            control = vote(scope, [*trustees, viewer], "The members approve another payment.")
            self.assertEqual(control[3]["count"]["kind"], "QUALIFIED",
                             "The wrong-body control must be a carried membership vote.")
        elif case == "defeated":
            control = vote(local, trustees, "Approve a different repair.", choices=["NOT_QUALIFIED"] * 3)
            self.assertEqual(control[3]["count"]["kind"], "NOT_QUALIFIED_VOTE",
                             "The defeated control must be an actual defeated Board vote.")
        marker = {
            "membership": str(control[3]["post_uuid"]) if control else None,
            "defeated": str(control[3]["post_uuid"]) if control else None,
            "publication": str(published[3]["post_uuid"]),
            "malformed": str(paid[3]["post_uuid"]) + " another reference",
            "absent": None,
        }[case]
        with self.subTest(citation=case):
            version, key, _, current_adoption = adopt(marker, label=case)
            page = show()
            only_adoption = expected(current_adoption[3], "standing_adoption", "adopted the Policy in force",
                                     binding=key, version=version)
            self.assertEqual(page["decisions"], [only_adoption],
                             "This in-force Policy must contribute only its permitted adoption, without reviving an older payment.")
            self.assertEqual(page["provisions"][-1]["binding"], key,
                             "The negative control must actually read this new binding as the governing duties source.")
            self.assertEqual(page["provisions"][-1]["clause"], approval,
                             "A rejected payment citation must preserve the independent office approval clause.")

    def test_each_office_part_has_its_own_audience(self):
        """R4a. Hidden and absent Topic openings preserve the same readable office siblings."""
        self._office_audience_case("topics")
        self._assert_observed_path_union("R4-topics")

    def test_office_pin_preserves_probationary_law_access(self):
        """R4b. An exact readable pin admits law without tenure, and hiding it removes admission."""
        self._office_audience_case("probation")
        self._assert_observed_path_union("R4-probation")

    def test_office_provisions_keep_exact_current_and_historic_law(self):
        """R4c. Unresolved and missing clauses preserve independently readable historical and current facts."""
        self._office_audience_case("laws")
        self._assert_observed_path_union("R4-laws")

    def test_only_the_cited_and_adoption_results_are_indexed(self):
        """R3a. Distinct payment/adoption receipts retain their independent result audiences."""
        self._office_decision_case("visible")
        self._assert_observed_path_union("R3")

    def test_office_rejects_membership_result_citation(self):
        """R3b. A carried membership result cannot supply a Board payment link."""
        self._office_decision_case("membership")

    def test_office_rejects_defeated_payment_citation(self):
        """R3c. A defeated Board result cannot supply the qualified payment link."""
        self._office_decision_case("defeated")

    def test_office_rejects_publication_as_payment(self):
        """R3d. A Policy publication result cannot replace a plain payment result."""
        self._office_decision_case("publication")

    def test_office_rejects_malformed_payment_citation(self):
        """R3e. A malformed current marker cannot revive an older valid citation."""
        self._office_decision_case("malformed")

    def test_office_omits_payment_after_marker_removed(self):
        """R3f. Removing the current marker removes the payment link while adoption remains."""
        self._office_decision_case("absent")

    def _history_fixture(self):
        """Read expected dates and keys from independent committed receipts, not the office projection."""
        from src.intevia.services import circle_commands, discussion_posts
        from tests.test_discussion_office_links import discussion_category, public_start_args
        owner, founder, office, definition, version, binding = approved_office(board_seats=0)
        circle = circle_commands.form(credential=founder.credential, organism_uuid=owner.organism_id,
                                      circle_uuid=uuid4(), kind="GENERIC", purpose="Discuss the office", request_uuid=uuid4())
        category = discussion_category(owner, founder, circle.circle_uuid)
        title = 'Human quotation: "The cited policy says the Treasurer approves ordinary payments"'
        args = public_start_args(owner, founder, circle, category)
        args.update(title=title, markdown="We quote CARE's words here as a human contribution about the office.")
        topic = discussion_posts.start_topic(**args, office_definition_uuid=office)
        definition_row = models.OrganismRoleDefinition.objects.get(role_uuid=office)
        peer = connection.copy()
        self.addCleanup(peer.close)
        with peer.cursor() as cur:
            cur.execute("""SELECT b.in_force_from,e.recorded_at,e.event_uuid FROM public.core_organismlawbinding b
                JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
                WHERE b.organism_id=%s AND b.binding_uuid=%s""", [owner.pk, binding])
            law_at, recorded, adopted = cur.fetchone()
            cur.execute("SELECT effective_at FROM public.core_livingorganismevent WHERE event_uuid=%s", [topic["act"]])
            topic_at = cur.fetchone()[0]
            cur.execute("SELECT content FROM public.l_clauses(%s) WHERE locator='approval'", [version.content])
            clause = cur.fetchone()[0]
        human = [
            {"kind": "law_adoption", "at": law_at, "label": "Office provision adopted", "source_link": binding,
             "ilc_link": adopted, "ilc_label": "View in ILC"},
            {"kind": "topic", "at": topic_at, "label": "Topic started: " + title, "source_link": topic["thread"],
             "ilc_link": topic["act"], "ilc_label": "View in ILC"},
        ]
        expected = {"office": {**expected_owner_labels(owner), "organism_uuid": owner.organism_id, "requested_definition": office,
                    "requested_version": definition_row.definition_version, "current_definition": office,
                    "current_version": definition_row.definition_version, "code": definition.code, "label": "Treasurer"},
                    "tenure": [{"state": "vacant"}], "decisions": [],
                    "provisions": [{"relationship": "OFFICE_BASIS", "status": "cited_and_in_force", "binding": binding,
                        "version": version.version_number, "adoption_record": adopted, "adopting_body": owner.organism_id,
                        "in_force_from": law_at, "recorded_at": recorded, "locator": "approval", "clause": clause,
                        "adoption_route": "Organisation Coordinator decision"}],
                    "topics": [{"title": title, "created_at": topic_at, "office_definition": office,
                        "office_version": definition_row.definition_version, "board_uuid": UUID(category["board"]),
                        "circle_uuid": circle.circle_uuid, "category_uuid": UUID(category["category"]),
                        "thread_link": topic["thread"]}]}
        return SimpleNamespace(owner=owner, founder=founder, office=office, definition=definition, version=version,
                               binding=binding, peer=peer, human=human, expected=expected, topic=topic, circle=circle)

    def _history_state(self, fixture):
        with fixture.peer.cursor() as cur:
            cur.execute("""SELECT o.head_event_id,
                coalesce((SELECT jsonb_agg(to_jsonb(a) ORDER BY a.version)
                    FROM public.core_careassistancesettingact a WHERE a.organism_id=o.id),'[]'::jsonb),
                coalesce((SELECT jsonb_agg(to_jsonb(d) ORDER BY d.event_uuid)
                    FROM public.core_careofficeobservation d WHERE d.organism_id=o.id),'[]'::jsonb),
                coalesce((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.sequence)
                    FROM public.core_livingorganismevent e WHERE e.living_organism_id=o.id
                     AND e.action IN ('SET_CARE_ASSISTANCE','RECORD_CARE_OFFICE_OBSERVATION')),'[]'::jsonb)
                FROM public.core_livingorganism o WHERE o.id=%s""", [fixture.owner.pk])
            row = cur.fetchone()
            return (row[0], *(json.loads(value) if isinstance(value, str) else value for value in row[1:]))

    def _history_read(self, fixture, present, expected, stamp):
        from unittest.mock import patch
        from src.intevia.services.office_record import _office_facts

        cuts = []

        def dated_facts(params, access, setting):
            facts = _office_facts(params, access, setting)
            self.assertEqual(facts["evaluated_at"], access["evaluated_at"],
                             "Office facts must report the database cut used to read them.")
            cuts.append(access["evaluated_at"])
            return {**facts, "evaluated_at": stamp}

        self.maxDiff = None
        before = self._history_state(fixture)
        earliest = database_now()
        with patch("src.intevia.services.office_record._office_facts", dated_facts):
            page = office_record(**display_args(fixture.owner, fixture.founder, fixture.office, present))
        latest = database_now()
        self.assertTrue(cuts and all(earliest <= cut <= latest for cut in cuts),
                        "Each office evaluation must use a database timestamp within this read.")
        self._assert_office_field_paths(page, named_chooser="switched_by_name" in expected["care"])
        self.assertEqual(page, expected,
                         "The complete office response must equal its independently evidenced facts, exact history and allowed CARE paths.")
        self.assertEqual(self._history_state(fixture), before,
                         "A preview, Off display or committed reuse must preserve full detail rows, both ILC action sets and the head.")
        for row in page["history"]:
            self.assertEqual(row["ilc_label"], "View in ILC", "Every office history row must name its ILC destination.")
            with fixture.peer.cursor() as cur:
                cur.execute("SELECT living_organism_id FROM public.core_livingorganismevent WHERE event_uuid=%s", [row["ilc_link"]])
                self.assertEqual(cur.fetchone(), (fixture.owner.pk,),
                                 "Every history link must resolve to an actual entry of the same organisation.")
        return page

    def _off_history_case(self, prior):
        fixture = self._history_fixture()
        owner, founder = fixture.owner, fixture.founder
        stamp = database_now()
        history = list(fixture.human)
        live = None
        observed = None
        setting = {"enabled": True, "choice_source": "default", "can_change": True}

        def expected(present):
            rows = history + ([observed] if setting["enabled"] and observed else [])
            return fixture.expected | {"evaluated_at": stamp,
                "care": setting | ({"observation": live} if setting["enabled"] and present and live else {}),
                "history": sorted(rows, key=lambda r: (r["at"], str(r["ilc_link"]), r["kind"]), reverse=True)}

        def choose(enabled, prior=None):
            nonlocal setting
            result = set_choice(owner, founder, enabled, prior=prior)
            key = result["act_uuid"]
            with fixture.peer.cursor() as cur:
                cur.execute("SELECT effective_at FROM public.core_careassistancesettingact WHERE act_uuid=%s", [key])
                at = cur.fetchone()[0]
                cur.execute("SELECT display_name FROM public.core_identityresolution WHERE identity_id=%s", [founder.pk])
                chooser_name = cur.fetchone()[0]
            setting = {"enabled": enabled, "choice_source": "chosen", "can_change": True,
                       "switch_receipt": key, "switched_by": founder.identity_id, "switched_by_name": chooser_name}
            history.append({"kind": "setting_change", "at": at,
                            "label": "CARE assistance turned " + ("On" if enabled else "Off"),
                            "source_link": key, "ilc_link": key, "ilc_label": "View in ILC",
                            "enabled": enabled, "switch_receipt": key})
            return key

        self._history_read(fixture, False, expected(False), stamp)
        if prior:
            positive = office_record(**display_args(owner, founder, fixture.office))
            self.assertIn("observation", positive["care"],
                          "The full Off comparison requires genuinely committed positive live guidance.")
            with fixture.peer.cursor() as cur:
                cur.execute("""SELECT event_uuid,sentence,provision_uuid,library_version_number,locator,qualification_at
                                 FROM public.core_careofficeobservation WHERE organism_id=%s""", [owner.pk])
                records = cur.fetchall()
            self.assertEqual(len(records), 1, "The positive fixture must contain exactly one committed observation.")
            event, words, source, version, locator, at = records[0]
            live = {"label": "CARE", "words": words, "event_ref": event, "source_version": version,
                    "source_locator": locator, "source_link": source}
            observed = {"kind": "care_observation", "at": at, "ilc_link": event, "ilc_label": "View in ILC", **live}
            baseline_observation = self._history_state(fixture)[2]
            self._history_read(fixture, True, expected(True), stamp)
            self._history_read(fixture, False, expected(False), stamp)
            on_key = choose(True)
            self._history_read(fixture, True, expected(True), stamp)
        else:
            baseline_observation = []
            on_key = None
        off_key = choose(False, on_key)
        for present in (False, True):
            self._history_read(fixture, present, expected(present), stamp)
        self.assertEqual(self._history_state(fixture)[2], baseline_observation,
                         "Off must preserve every stored observation byte, including the empty-history case.")
        choose(True, off_key)
        self._history_read(fixture, False, expected(False), stamp)
        if prior:
            self._history_read(fixture, True, expected(True), stamp)
        self.assertEqual(self._history_state(fixture)[2], baseline_observation,
                         "On again must reuse the unchanged observation and a preview must never create one.")

    def test_off_removes_only_care_output(self):
        """R5. Whole default/explicit On, Off, preview and On-again pages keep human facts and exact committed history."""
        self._off_history_case(True)
        self._assert_observed_path_union("R5")

    def test_off_without_an_observation_keeps_human_history(self):
        """R5-empty. Both Off flags preserve human quotations and links while a later On preview remains unwritten."""
        self._off_history_case(False)
        self._assert_observed_path_union("R5-empty")

    def test_historic_guidance_uses_its_own_clause(self):
        """R4-history. A successor clause stops live guidance but retains the permitted old receipt only in On history."""
        fixture = self._history_fixture()
        first = office_record(**display_args(fixture.owner, fixture.founder, fixture.office))
        original = next(row for row in first["history"] if row["kind"] == "care_observation")
        self.assertIn("observation", first["care"], "The historical case must begin with qualifying committed guidance.")
        successor = record_office(fixture.owner, fixture.founder, fixture.definition, prior=fixture.office)
        version, binding, receipt = a1_adopt_policy(fixture.owner, fixture.founder,
            fixture.version.resource.resource_id, fixture.version.content + " The Board approves payments during a vacancy.",
            prior_version=fixture.version, prior_binding=fixture.binding)
        self.assertEqual(receipt.status, "ADOPT", "A genuine successor must place the changed clause in force.")
        before = self._history_state(fixture)
        current = office_record(**display_args(fixture.owner, fixture.founder, fixture.office))
        self.assertEqual(current["office"]["current_definition"], successor,
                         "The old office URL must resolve its successor before selecting historical source evidence.")
        self._assert_office_field_paths(current)
        self.assertNotIn("observation", current["care"], "A changed current clause must not be presented as live Alpha guidance.")
        self.assertEqual([r for r in current["history"] if r["kind"] == "care_observation"], [original],
                         "The permitted historical row must retain its original clause, event and date after a successor governs.")
        self.assertEqual(self._history_state(fixture), before,
                         "Reading historical guidance must not create a second pair or alter either history.")
        off = set_choice(fixture.owner, fixture.founder, False)
        baseline = self._history_state(fixture)
        hidden = office_record(**display_args(fixture.owner, fixture.founder, fixture.office))
        self.assertFalse(any(r["kind"] == "care_observation" for r in hidden["history"]),
                         "Off must suppress even non-current CARE history and its source links.")
        self.assertEqual(self._history_state(fixture), baseline, "An Off historical read must leave all stored bytes unchanged.")
        set_choice(fixture.owner, fixture.founder, True, off["act_uuid"])
        baseline = self._history_state(fixture)
        restored = office_record(**display_args(fixture.owner, fixture.founder, fixture.office))
        self.assertEqual([r for r in restored["history"] if r["kind"] == "care_observation"], [original],
                         "On again must restore the same authorised historical row even though its source no longer qualifies live.")
        self.assertNotIn("observation", restored["care"], "Turning On must not revive obsolete live guidance.")
        self.assertEqual(self._history_state(fixture), baseline, "Historical restoration is a read of the original pair.")

        self._assert_observed_path_union("R4-history")

    def _assert_observed_path_union(self, case):
        self.assertEqual(set(OFFICE_PATH_GROUPS), set().union(*map(set, OFFICE_PATH_CASES.values())),
                         "Every allowed public-field group must belong to a real emitting test case.")
        expected = set().union(*(OFFICE_PATH_GROUPS[name] for name in OFFICE_PATH_CASES[case]))
        self.assertEqual(self._office_observed_paths, expected,
                         "The union of actual positive, negative and empty response paths must exactly match this case's independent map.")

    def _assert_office_field_paths(self, page, *, pin_only=False, named_chooser=False):
        """5B's independent closed key sets; value and audience assertions accompany each calling case."""
        seen = getattr(self, "_office_observed_paths", set())

        def visit(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    visit(child, path + ("." if path else "") + key)
            elif isinstance(value, list):
                seen.add(path + "[]")
                for child in value:
                    variant = child["kind"] if path == "history" else child["state"] if path == "tenure" else ""
                    visit(child, path + "[" + variant + "]")
            else:
                seen.add(path)

        visit(page, "")
        self._office_observed_paths = seen
        self.assertEqual(set(page), {"evaluated_at", "office", "care", "tenure", "history", "provisions", "topics", "decisions"},
                         "The office envelope must not acquire an unexamined public field.")
        office = {"organism_uuid", "requested_definition", "requested_version", "organisation_full_name", "organisation_short_label"}
        if not pin_only:
            office |= {"code", "label", "current_definition", "current_version", "board_full_name", "board_short_label"}
        self.assertEqual(set(page["office"]), office, "Office identifiers must match this reader's exact admission route.")
        for term in page["tenure"]:
            self.assertIn(term["state"], {"vacant", "ongoing", "former"}, "Tenure must have an established public state.")
            fields = {"state"} if term["state"] == "vacant" else {"state", "starts_at", "ends_at"}
            if "holder_name" in term:
                fields.add("holder_name")
            self.assertEqual(set(term), fields, "A term may expose its permitted name and dates, never private receipt or person links.")
        for provision in page["provisions"]:
            fields = {"relationship", "status", "binding", "version", "adoption_record", "adopting_body",
                      "in_force_from", "recorded_at", "clause", "adoption_route"}
            for optional in ("locator", "adoption_result_link"):
                if optional in provision:
                    fields.add(optional)
            self.assertEqual(set(provision), fields, "A provision must contain only its independently readable exact law and adoption fields.")
        for topic in page["topics"]:
            self.assertEqual(set(topic), {"title", "created_at", "office_definition", "office_version", "board_uuid",
                                          "circle_uuid", "category_uuid", "thread_link"},
                             "Topic history must not create another route to hidden opening data.")
        for decision in page["decisions"]:
            self.assertIn(decision["purpose"], {"standing_adoption", "one_payment"}, "Only the two governed result relationships exist here.")
            fields = {"purpose", "label", "body", "result_uuid", "outcome", "effective_at", "recorded_at", "result_link"}
            if decision["purpose"] == "standing_adoption":
                fields |= {"adopted_binding", "adopted_version"}
            if "reporting_rule_link" in decision:
                fields.add("reporting_rule_link")
            self.assertEqual(set(decision), fields, "A decision row must not disclose an unexamined result field or private source.")
        setting = {"enabled", "choice_source", "can_change"}
        self.assertIn(page["care"]["choice_source"], {"default", "chosen"}, "The setting must distinguish default from recorded choice.")
        if page["care"]["choice_source"] == "chosen":
            setting |= {"switch_receipt", "switched_by"}
            if named_chooser:
                setting.add("switched_by_name")
        if "observation" in page["care"]:
            setting.add("observation")
            self.assertEqual(set(page["care"]["observation"]),
                             {"label", "words", "event_ref", "source_version", "source_locator", "source_link"},
                             "Live guidance must have only its permitted words and exact source coordinates.")
        self.assertEqual(set(page["care"]), setting, "A CARE choice must not include an extra source or identity field.")
        variants = {
            "law_adoption": set(), "topic": set(), "decision": set(),
            "setting_change": {"enabled", "switch_receipt"},
            "care_observation": {"words", "event_ref", "source_version", "source_locator"},
        }
        for row in page["history"]:
            self.assertIn(row["kind"], variants, "A new history variant needs its own governed public contract and witness.")
            fields = {"kind", "at", "label", "source_link", "ilc_link", "ilc_label"} | variants[row["kind"]]
            self.assertEqual(set(row), fields, "History must show what happened, when and its ILC link, without an Enterer field.")
            self.assertEqual(row["ilc_label"], "View in ILC", "Every history destination must be labelled View in ILC.")
            if row["kind"] == "care_observation":
                self.assertEqual(row["label"], "CARE", "CARE-authored history must carry the CARE label.")

    def test_whole_charter_keeps_its_inherited_ilc_destination(self):
        """R4-charter. A protected whole-document citation keeps inherited text and its real root ILC entry."""
        from pathlib import Path
        from src.intevia.services import charter_entry, root_genesis
        from src.intevia.services.contribution_authority import ContributionAuthority
        from src.intevia.services.organism_role_definition_service import DefinitionContent, DefinitionProvision
        from tests import root_genesis_support
        from tests.test_law_records import LibraryFixtureCapability

        owner, founder = law.ordinary_birth(board_seats=0)
        root = root_genesis.execute(root_genesis_support.command())
        author = root.founder_identity
        role, _ = models.Role.objects.get_or_create(name="LAW library fixture role")
        models.ProfileRole.objects.get_or_create(identity=author, role=role)
        charter_entry.enter_constitutional_charters(credential=author.credential,
            library_authority=ContributionAuthority(LibraryFixtureCapability()), request_id=uuid4())
        charter = charter_entry.CHARTERS[0]
        law_row = next(row for row in library_laws.our_laws(credential=founder.credential,
                         organism_uuid=owner.organism_id)["laws"] if row["library_reference"] == charter.resource)
        self.assertTrue(law_row["inherited"], "The whole-charter fixture must be inherited from the platform root.")
        exact = (Path(__file__).resolve().parents[1] / "docs/constitution" / charter.filename).read_bytes().decode("utf-8")
        self.assertEqual(law_row["content"], exact, "The independent LAW reader must preserve the ratified whole text exactly.")
        definition = DefinitionContent(code="TREASURER", provisions=(DefinitionProvision(
            "OFFICE_BASIS", "law-ref:" + charter.resource, str(law_row["version_number"])),))
        office = record_office(owner, founder, definition)
        role_row = models.OrganismRoleDefinition.objects.get(role_uuid=office)
        peer = connection.copy()
        self.addCleanup(peer.close)
        fixture = SimpleNamespace(owner=owner, peer=peer)
        before = self._history_state(fixture)
        earliest = database_now()
        page = office_record(**display_args(owner, founder, office))
        self.assertTrue(earliest <= page["evaluated_at"] <= database_now(), "The whole-charter read must report its own database time.")
        expected = {"evaluated_at": page["evaluated_at"],
            "office": {**expected_owner_labels(owner), "organism_uuid": owner.organism_id, "requested_definition": office,
                       "requested_version": role_row.definition_version, "code": "TREASURER", "label": "Treasurer",
                       "current_definition": office, "current_version": role_row.definition_version},
            "care": {"enabled": True, "choice_source": "default", "can_change": True},
            "tenure": [{"state": "vacant"}], "topics": [], "decisions": [],
            "provisions": [{"relationship": "OFFICE_BASIS", "status": "cited_and_in_force", "binding": law_row["binding"],
                            "version": law_row["version_number"], "adoption_record": law_row["act"],
                            "adopting_body": root.organism_id, "in_force_from": law_row["in_force_from"],
                            "recorded_at": law_row["adoption_recorded_at"], "clause": exact,
                            "adoption_route": "Founder Steward decision"}],
            "history": [{"kind": "law_adoption", "at": law_row["in_force_from"], "label": "Office provision adopted",
                         "source_link": law_row["binding"], "ilc_link": law_row["act"], "ilc_label": "View in ILC"}]}
        self._assert_office_field_paths(page)
        self.assertEqual(page, expected, "The inherited whole charter must keep exact bytes and root attribution without inventing a local adoption or locator.")
        with peer.cursor() as cur:
            cur.execute("SELECT living_organism_id FROM public.core_livingorganismevent WHERE event_uuid=%s", [law_row["act"]])
            self.assertEqual(cur.fetchone(), (root.pk,), "The charter's ILC link must open its actual root entry, not a fabricated local event.")
        self.assertEqual(self._history_state(fixture), before, "A whole-charter office read must create no CARE pair or local chain entry.")
        self._assert_observed_path_union("R4-charter")

    def test_history_failure_delivers_facts_after_rollback(self):
        """R4-fallback. Failure reading optional stored CARE history delivers one committed human-facts response."""
        fixture = self._history_fixture()
        positive = office_record(**display_args(fixture.owner, fixture.founder, fixture.office))
        self.assertIn("observation", positive["care"], "The history-failure witness requires a healthy committed positive observation.")
        before = self._history_state(fixture)
        faults = []
        delivered = []

        def fail_history(execute, statement, params, many, context):
            if "o.library_version_id,o.adoption_uuid" in str(statement):
                faults.append(True)
                return execute("SELECT 1/0", None, False, context)
            return execute(statement, params, many, context)

        earliest = database_now()
        with connection.execute_wrapper(fail_history):
            publish_office_record(**display_args(fixture.owner, fixture.founder, fixture.office),
                                  publish=lambda page: delivered.append((connection.in_atomic_block, page)))
        self.assertEqual(faults, [True], "The real database failure must occur once at the optional history read and not recur in fallback.")
        self.assertEqual(len(delivered), 1, "Only the successful fallback transaction may deliver a response after the history error.")
        in_transaction, page = delivered[0]
        self.assertFalse(in_transaction, "The fallback must reach the consumer only after its outer commit.")
        self.assertTrue(earliest <= page["evaluated_at"] <= database_now(), "Fallback must report a newly read database time.")
        self._assert_office_field_paths(page)
        expected = fixture.expected | {"evaluated_at": page["evaluated_at"],
            "care": {"enabled": True, "choice_source": "default", "can_change": True},
            "history": sorted(fixture.human, key=lambda r: (r["at"], str(r["ilc_link"]), r["kind"]), reverse=True)}
        self.assertEqual(page, expected, "A history failure must deliver all permitted human facts, without live or historical CARE output.")
        self.assertEqual(self._history_state(fixture), before, "The failed read and fallback must preserve full committed rows and the organisation head.")
        self._assert_observed_path_union("R4-fallback")
