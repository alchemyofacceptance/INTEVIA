"""TIMED EVENTS engine through a real consumer: a Board office term end (ELECT).

The module is also the URL configuration for TE-01's test entry: two routes that exist
only here, added to the product's own routes. No route on main calls an LO-scoped
service yet, so the handoff is proved through a view written as the front end will
write them.
"""

import json
import threading
import time
from dataclasses import replace
from datetime import timedelta
from unittest import mock
from uuid import uuid4

from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler
from django.db import IntegrityError, connection, connections, transaction
from django.db.utils import ConnectionDoesNotExist
from django.http import JsonResponse
from django.test import Client, RequestFactory, TransactionTestCase, override_settings
from django.urls import path

from core import models
from core.session import (
    SESSION_ACCESS_EPOCH_KEY,
    SESSION_IDENTITY_KEY,
    SESSION_STARTED_AT_KEY,
)
from intevia.urls import urlpatterns as product_urlpatterns
from src.intevia.services import cq, elections
from src.intevia.services import discussion as disc
from src.intevia.services import organism_recovery
from src.intevia.services import timed_events as te
from src.intevia.services import timed_events_handoff as handoff
from tests import cq_authority_support as board
from tests import cq_settings_support
from tests import election_support as fx
from tests import s015_0021_support as support
from tests import test_cq_clarity as clarity
from tests import test_law_acts as law
from tests import test_sr_office_actions as office

WAIT = 30  # seconds: an upper bound on an observed condition, never a sleep


def _rows(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def _wake(lo, person):
    return te.Wake(str(lo.organism_id), person.pk, person.credential_id, person.access_epoch)


def _ended_terms(names, *, shared_end=False):
    """An LO in which each named Board office has just reached its fixed end, with nothing yet recorded for it.

    Returns the LO, its founder, the office holder, the founder's scope, and for each
    office in order its assignment and its end. The ends are in the order given, or,
    with ``shared_end``, one and the same instant for every office.
    """
    from src.intevia.services import organism_role_definition_service as definitions
    from src.intevia.services import organism_role_service as roles

    rules = {fx.ARTICLE: fx.seat()} | {
        name: {"type": "OFFICE", "place": name.upper(), "body": "LO_MEMBERSHIP"} for name in names}
    lo, founder, scope, _circle, citation = fx.owner(rules=rules, voting=False)
    holder = law.add_member(lo)
    helper = office.SROfficeActionTests()
    helper.actor, helper.organism = founder, lo
    ended = []
    for name in names:
        content = definitions.DefinitionContent(code=name.upper(), provisions=(
            definitions.DefinitionProvision("OFFICE_BASIS", name, str(citation["version_id"])),))
        version = definitions.OrganismRoleDefinitionService(
            authority=board.authority(), clock=support.database_now).record_role_definition(helper._command(content=content))
        role = models.OrganismRoleDefinition.objects.get(role_uuid=version.role_definition_uuid)
        assignment = fx.office_proposal(lo, founder, holder, role, self_proposed=False)
        if name == "Treasurer":
            models.EssentialCoverageRequirement.objects.create(
                living_organism=lo, roster_version=1, responsibility_domain_code="TREASURY",
                qualifying_role_code="TREASURER", scope=role.scope, minimum_active_occupants=1,
                effective_from=support.database_now())
        ended.append((assignment, helper._transition_command(assignment, "ASSIGN")))
    service = roles.OrganismRoleService(authority=board.authority(), clock=support.database_now)
    common = max([support.database_now()] + [command.effective_at + timedelta(microseconds=1) for _, command in ended])
    for index, (assignment, command) in enumerate(ended):
        office_end = common if shared_end else max(support.database_now(), command.effective_at + timedelta(microseconds=1))
        service.transition_assignment(replace(command, effective_until_state="FIXED", effective_until=office_end))
        ended[index] = (assignment, office_end)
    return lo, founder, holder, scope, ended


def _ended_treasury_term():
    """An LO whose recorded Treasurer term has just reached its fixed end, with nothing yet recorded for it."""
    lo, founder, treasurer, scope, ((assignment, office_end),) = _ended_terms(("Treasurer",))
    return lo, founder, treasurer, scope, assignment, office_end


def _owing_a_notice():
    """An LO that owes C2 one coverage notice (the C2-06 fixture). Returns its id, the notice's source and occurrence."""
    start = support.database_now() - timedelta(days=75)
    with transaction.atomic(), connection.cursor() as cursor:
        ids = support.found_nonroot(cursor, uuid4().hex, fixture_at=start, founding_assignment_overrides={
            "effective_until_state": "FIXED", "effective_until": start + timedelta(days=5)})
    (source, occurrence), = _rows("SELECT source_ref, occurrence FROM public.te_c2_notice_due(%s, statement_timestamp())", [ids["lo"]])
    return ids["lo"], source, occurrence


def _notice(lo, source, *, using=connection):
    """Make the notice with the landed writer, under the LO lock, on the caller's transaction."""
    with using.cursor() as cursor:
        cursor.execute("SELECT 1 FROM public.core_livingorganism WHERE id=%s FOR UPDATE", [lo])
        cursor.execute("SELECT public.c2_notice_input(%s,%s)", [lo, source])
        return cursor.fetchone()[0]


def _provenance(lo, source, occurrence):
    """What this transaction can see of the notice's assessment: None, or (made here by the helper, made here by the proof)."""
    rows = _rows("""SELECT public.te_made_here('core_coverageassessment', a.event_uuid::text),
            (SELECT p.made_here FROM public.te_c2_proof(%s,'NOTICE',%s,%s,a.event_uuid::text) p)
        FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
        WHERE c.living_organism_id=%s AND a.evidence_reference='c2-boundary:'||%s""", [lo, source, occurrence, lo, source])
    return rows[0] if rows else None


class _Undo(Exception):
    """Raised inside a savepoint to roll it back."""


def _engine_facts(organism_uuid):
    """For one LO: receipts, recorded office term ends, probes, and skips counted against the LO."""
    return list(_rows("""SELECT
        (SELECT count(*) FROM public.core_tereceipt r WHERE r.organism_id=lo.id),
        (SELECT count(*) FROM public.core_contextualroleassignmentstateevent e
            JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
            JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
            WHERE d.living_organism_id=lo.id AND e.action='END'),
        (SELECT coalesce(sum(p.probes),0)::bigint FROM public.core_tepace p WHERE p.organism_id=lo.id),
        (SELECT coalesce(sum(m.busy_lo+m.busy_inner+m.in_transaction+m.not_eligible+m.no_limits
            +m.commit_failed+m.errors),0)::bigint FROM public.core_temeter m WHERE m.organism_id=lo.id)
        FROM public.core_livingorganism lo WHERE lo.organism_id=%s""", [organism_uuid])[0])


def _signed_in(person):
    """A client whose session already exists and is bound to this identity. No sign-in request is made."""
    client = Client()
    client.force_login(person.credential)
    session = client.session
    session[SESSION_IDENTITY_KEY] = str(person.identity_id)
    session[SESSION_ACCESS_EPOCH_KEY] = person.access_epoch
    session[SESSION_STARTED_AT_KEY] = int(time.time())
    session.save()
    return client


def _scoped_entry(request, organism):
    """Test entry. It reads through a landed LO-scoped service and confirms on its success path only."""
    try:
        cq.settings(credential=request.user, organism_uuid=organism)
    except disc.DiscussionUnavailable:
        return JsonResponse({"refused": "service"})  # a handled refusal: no confirm
    if request.GET.get("outcome") == "failed":
        return JsonResponse({"refused": "view"})  # the view's own failure branch: marked, never confirmed
    handoff.confirm()
    return JsonResponse({"inside": _engine_facts(organism)})


def _unscoped_entry(request):
    """Test entry. A successful page that touches no LO."""
    handoff.confirm()
    return JsonResponse({"unscoped": True})


urlpatterns = [
    path("te-test/scoped/<uuid:organism>/", _scoped_entry),
    path("te-test/unscoped/", _unscoped_entry),
    *product_urlpatterns,
]


class TimedEventsTests(TransactionTestCase):
    topic = clarity.CQClarityTests.topic
    configure = cq_settings_support.configure

    def test_once_including_rollback(self):
        """TE-02. For one due occurrence, failing and competing attempts leave exactly one effect and one receipt, holding the original due time and the later actual opening.

        Cases, each on committed, independent connections: a failure between the effect and
        its receipt leaves neither; a competing attempt that finds the LO busy gets nothing
        and is counted against no consumer; the attempt that completes records one linked
        effect and receipt; a replay and the landed catch-up record nothing more; a second,
        otherwise valid receipt for the same occurrence is refused by storage. Text from an
        unknown refusal is kept nowhere. A late claim to have opened the effect is refused.
        An attempt that finds a row of its claim held elsewhere ends at once with nothing:
        an office holder's identity, which the committee refresh reads at commit; and the
        authority basis the term end's records will cite, which their foreign keys read at
        commit; and the office state event table itself, held against writers as the landed
        platform-law check holds it.
        """
        lo, founder, treasurer, scope, assignment, office_end = _ended_treasury_term()
        events = models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment)
        source = events.get(action="ASSIGN")
        key = [lo.pk, "ELECT", "TERM_END", str(assignment.assignment_uuid), str(source.event_uuid)]

        def receipts():
            return _rows("""SELECT outcome, effect_ref, rule_ref, enterer_id, due_at, effect_at, discovered_at
                FROM public.core_tereceipt WHERE organism_id=%s AND consumer=%s AND purpose=%s
                AND source_ref=%s AND occurrence=%s""", key)

        def pace():
            return _rows("""SELECT probes, opened, found_done, blocked, failed, last_failure_class, last_failure_code
                FROM public.core_tepace WHERE organism_id=%s AND consumer='ELECT'""", [lo.pk])

        def busy_skips():
            return _rows("SELECT busy_lo, busy_inner FROM public.core_temeter WHERE organism_id=%s", [lo.pk])

        # 1. A failure exactly between the effect and its receipt leaves neither. The failure is a
        # database refusal whose message has the shape of a code but is no refusal the writer is known to give.
        marker = "TE02_UNKNOWN_MARKER"

        def unknown_refusal(_opening):
            _rows("DO $$ BEGIN RAISE EXCEPTION '" + marker + "' USING ERRCODE='23514'; END $$")

        with mock.patch.object(te, "_between_effect_and_receipt", side_effect=unknown_refusal):
            failed = te.run(_wake(lo, founder), alias="default")
        self.assertEqual((failed.result, failed.purpose, failed.failure_class, failed.code),
                         ("FAILED", "TERM_END", "ERROR", "23"),
                         "TE-02 failure: the attempt must report the failed act by its error class, not an opening.")
        self.assertFalse(events.filter(action="END").exists(),
                         "TE-02 failure: the term end must not survive without its receipt.")
        self.assertEqual(receipts(), [], "TE-02 failure: no receipt may exist without its effect.")
        self.assertEqual(pace(), [(1, 0, 0, 0, 1, "ERROR", "23")],
                         "TE-02 failure: the probe and the failure class must survive the rolled-back act.")
        kept = _rows("SELECT to_jsonb(p)::text FROM public.core_tepace p WHERE p.organism_id=%s", [lo.pk])
        kept += _rows("SELECT to_jsonb(m)::text FROM public.core_temeter m WHERE m.organism_id=%s", [lo.pk])
        self.assertNotIn(marker, repr(failed) + repr(kept),
                         "TE-02 failure: text from an unknown refusal must survive in no record and no return.")

        # 1b. A row the committee refresh would read-lock at commit is held by another connection: the
        # identity of an office holder of this LO. The term end's claim is then not free, so the attempt
        # ends at once: no END, no receipt, counted as a busy inner row, charged to no consumer.
        held, let_go = threading.Event(), threading.Event()

        def hold_office_holder():
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM public.core_identity WHERE id=%s FOR UPDATE", [treasurer.pk])
                    held.set()
                    let_go.wait(WAIT)
            finally:
                connections.close_all()

        fx.idle_until_turn_of(lo, founder, "ELECT")
        holder_thread = threading.Thread(target=hold_office_holder)
        holder_thread.start()
        try:
            self.assertTrue(held.wait(WAIT), "TE-02 held row: the other connection never took the identity row.")
            skipped = te.run(_wake(lo, founder), alias="default")
            self.assertTrue(holder_thread.is_alive(), "TE-02 held row: the engine must return while the row is still held.")
            self.assertEqual((skipped.result, skipped.consumer), ("BUSY_INNER", None),
                             "TE-02 held row: with a row of the refresh claim held elsewhere the engine must skip at once.")
            self.assertEqual((events.filter(action="END").exists(), receipts(), busy_skips(), pace()),
                             (False, [], [(0, 1)], [(1, 0, 0, 0, 1, "ERROR", "23")]),
                             "TE-02 held row: the skip must leave no END and no receipt, and be charged to no consumer.")
        finally:
            let_go.set()
            holder_thread.join(WAIT)
        self.assertFalse(holder_thread.is_alive(), "TE-02 held row: the other connection did not finish.")

        # 1c. The authority basis the END event and its coverage records will cite is held by another
        # connection, as the landed authority lifecycle holds one (0056). Their foreign keys would wait for
        # it at commit, with the LO held. The claim tries it first, so the attempt ends at once.
        basis = _rows("SELECT authority_basis_id FROM public.core_contextualroleassignmentstateevent WHERE id=%s", [source.pk])[0][0]
        held, let_go = threading.Event(), threading.Event()

        def hold_basis():
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM public.core_authoritybasis WHERE id=%s FOR UPDATE", [basis])
                    held.set()
                    let_go.wait(WAIT)
            finally:
                connections.close_all()

        fx.idle_until_turn_of(lo, founder, "ELECT")
        holder_thread = threading.Thread(target=hold_basis)
        holder_thread.start()
        try:
            self.assertTrue(held.wait(WAIT), "TE-02 held basis: the other connection never took the authority basis.")
            skipped = te.run(_wake(lo, founder), alias="default")
            self.assertTrue(holder_thread.is_alive(), "TE-02 held basis: the engine must return while the basis is still held.")
            self.assertEqual((skipped.result, skipped.consumer), ("BUSY_INNER", None),
                             "TE-02 held basis: with the basis its records will cite held elsewhere the engine must skip at once.")
            self.assertEqual((events.filter(action="END").exists(), receipts(), busy_skips(), pace()),
                             (False, [], [(0, 2)], [(1, 0, 0, 0, 1, "ERROR", "23")]),
                             "TE-02 held basis: the skip must leave no END and no receipt, and be charged to no consumer.")
        finally:
            let_go.set()
            holder_thread.join(WAIT)
        self.assertFalse(holder_thread.is_alive(), "TE-02 held basis: the other connection did not finish.")

        # 1d. The whole office state event table is held against writers by another connection, in the
        # mode the landed platform-law check takes (l_lock_dependencies, 0067: SHARE). No row of this LO
        # is held. The term end's insert would wait for the table, with the LO held. The claim tries the
        # table lock first, before the LO, so the attempt ends at once.
        held, let_go = threading.Event(), threading.Event()

        def hold_table():
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("LOCK TABLE public.core_contextualroleassignmentstateevent IN SHARE MODE")
                    held.set()
                    let_go.wait(WAIT)
            finally:
                connections.close_all()

        fx.idle_until_turn_of(lo, founder, "ELECT")
        holder_thread = threading.Thread(target=hold_table)
        holder_thread.start()
        try:
            self.assertTrue(held.wait(WAIT), "TE-02 held table: the other connection never took the table lock.")
            skipped = te.run(_wake(lo, founder), alias="default")
            self.assertTrue(holder_thread.is_alive(), "TE-02 held table: the engine must return while the table is still held.")
            self.assertEqual((skipped.result, skipped.consumer), ("BUSY_INNER", None),
                             "TE-02 held table: with the office state event table held against writers the engine must skip at once.")
            self.assertEqual((events.filter(action="END").exists(), receipts(), busy_skips(), pace()),
                             (False, [], [(0, 3)], [(1, 0, 0, 0, 1, "ERROR", "23")]),
                             "TE-02 held table: the skip must leave no END and no receipt, and be charged to no consumer.")
        finally:
            let_go.set()
            holder_thread.join(WAIT)
        self.assertFalse(holder_thread.is_alive(), "TE-02 held table: the other connection did not finish.")

        # 2. A competing attempt arrives while another attempt holds the LO mid-act.
        inside, release, winner = threading.Event(), threading.Event(), []

        def hold_between(_opening):
            inside.set()
            if not release.wait(WAIT):
                raise RuntimeError("TE-02 winner was never released")

        def win():
            try:
                winner.append(te.run(_wake(lo, treasurer), alias="default"))
            finally:
                connections.close_all()

        fx.idle_until_turn_of(lo, founder, "ELECT")  # one consumer per wake: the others take their turn first
        with mock.patch.object(te, "_between_effect_and_receipt", side_effect=hold_between):
            thread = threading.Thread(target=win)
            thread.start()
            try:
                self.assertTrue(inside.wait(WAIT), "TE-02 competition: the first attempt never reached its act.")
                loser = te.run(_wake(lo, founder), alias="default")
                self.assertEqual((loser.result, loser.consumer), ("BUSY_LO", None),
                                 "TE-02 competition: the attempt that finds the LO busy must end at once with nothing.")
                self.assertEqual(receipts(), [], "TE-02 competition: the busy attempt must not write a receipt.")
                self.assertEqual(busy_skips(), [(1, 3)], "TE-02 competition: the busy skip must be counted against the LO.")
                self.assertEqual(pace(), [(1, 0, 0, 0, 1, "ERROR", "23")],
                                 "TE-02 competition: a busy skip must not be charged to any consumer.")
            finally:
                release.set()
                thread.join(WAIT)
        self.assertFalse(thread.is_alive(), "TE-02 competition: the first attempt did not finish.")
        self.assertEqual([(w.result, w.purpose, w.failure_class, w.code) for w in winner],
                         [("OPENED", "TERM_END", None, None)],
                         "TE-02 competition: the attempt holding the LO must complete its opening.")

        # 3. Exactly one effect and one linked receipt: the original due time and the later opening.
        ended = events.get(action="END")
        enterer = _rows("SELECT enterer_identity_id FROM public.core_contextualroleassignmentstateevent WHERE id=%s",
                        [ended.pk])[0][0]
        self.assertEqual((ended.effective_at, ended.evidence_reference, enterer),
                         (office_end, "elect-expiry:" + str(source.event_uuid), treasurer.pk),
                         "TE-02 once: the END event is dated at the boundary, cites its ASSIGN event, and names "
                         "the member whose request woke the engine as its enterer.")
        self.assertEqual([row[:6] for row in receipts()],
                         [("OPENED", str(ended.event_uuid), "ELECT:TERM_END", treasurer.pk, office_end, ended.recorded_at)],
                         "TE-02 once: the receipt must cite this END event, its rule, its enterer and both times.")
        self.assertGreater(ended.recorded_at, office_end, "TE-02 once: the opening is later than the due time.")
        self.assertGreaterEqual(receipts()[0][6], ended.recorded_at, "TE-02 once: discovery is not before the effect.")
        self.assertEqual(pace(), [(2, 1, 0, 0, 1, "ERROR", "23")])

        # 4. A replay and the landed catch-up add nothing.
        replay = te.run(_wake(lo, founder), alias="default")
        self.assertEqual((replay.result, elections.catch_up_terms(**scope)), ("IDLE", 0))
        self.assertEqual((events.filter(action="END").count(), len(receipts())), (1, 1),
                         "TE-02 replay: one effect and one receipt for the occurrence.")

        # 5. Storage itself refuses a second, otherwise valid receipt for the same occurrence.
        with self.assertRaises(IntegrityError, msg="TE-02 uniqueness: storage must refuse a second receipt for "
                               "the same LO, consumer, purpose, source and occurrence.") as refused, transaction.atomic():
            _rows("""INSERT INTO public.core_tereceipt (organism_id, consumer, purpose, source_ref, occurrence,
                    outcome, effect_ref, rule_ref, enterer_id, due_at, effect_at)
                SELECT organism_id, consumer, purpose, source_ref, occurrence, 'FOUND_DONE', effect_ref,
                    rule_ref, enterer_id, due_at, effect_at FROM public.core_tereceipt
                WHERE organism_id=%s AND consumer=%s AND purpose=%s AND source_ref=%s AND occurrence=%s
                RETURNING receipt_uuid""", key)
        self.assertEqual(refused.exception.__cause__.diag.constraint_name, "te_one_receipt")
        # ... and refuses, before that, a late claim to have opened an effect an earlier transaction made.
        with self.assertRaises(IntegrityError) as late_claim, transaction.atomic():
            _rows("""INSERT INTO public.core_tereceipt (organism_id, consumer, purpose, source_ref, occurrence,
                    outcome, effect_ref, rule_ref, enterer_id, due_at, effect_at)
                SELECT organism_id, consumer, purpose, source_ref, occurrence, 'OPENED', effect_ref,
                    rule_ref, enterer_id, due_at, effect_at FROM public.core_tereceipt
                WHERE organism_id=%s AND consumer=%s AND purpose=%s AND source_ref=%s AND occurrence=%s
                RETURNING receipt_uuid""", key)
        self.assertEqual(late_claim.exception.__cause__.diag.message_primary, "TE_RECEIPT_ORIGIN",
                         "TE-02 storage: only the transaction that made an effect may record it as opened.")
        self.assertEqual(len(receipts()), 1)

    @override_settings(ROOT_URLCONF="tests.test_timed_events")
    def test_activity_boundary(self):
        """TE-01. A successful, confirmed, signed-in request opens at most one due item in its own LO after its response closes, and nothing else does.

        Cases, each through the real middleware and a real response close: the host switch
        off; a refusal the view handled; a marked request the view did not confirm; a later
        confirmed request that touches no LO; a session the landed gate turns away; the
        opening, with nothing opened inside the request and exactly one after its close;
        the same with Django's own connection cleanup running first, as on a server; an
        attempt that finds its connection inside a transaction.
        """
        lo, founder, holder, _scope, ((treasury, treasury_end), (secretary, secretary_end)) = _ended_terms(
            ("Treasurer", "Secretary"))
        other = _ended_treasury_term()[0]
        client = _signed_in(founder)
        scoped = f"/te-test/scoped/{lo.organism_id}/"

        def facts():
            return _engine_facts(lo.organism_id), _engine_facts(other.organism_id)

        def openings():
            return _rows("""SELECT r.source_ref, r.outcome, r.enterer_id, r.due_at FROM public.core_tereceipt r
                WHERE r.organism_id=%s ORDER BY r.discovered_at""", [lo.pk])

        untouched = [0, 0, 0, 0]
        self.assertLess(treasury_end, secretary_end)

        # The host switch is off unless a host turns it on.
        self.assertIs(settings.INTEVIA_TIMED_EVENTS_WAKE, False)
        self.assertEqual(client.get(scoped).json(), {"inside": untouched})
        self.assertEqual(facts(), (untouched, untouched), "TE-01 switch off: a confirmed request must wake nothing.")

        with self.settings(INTEVIA_TIMED_EVENTS_WAKE=True):
            # Requests that must leave nothing for the engine: no probe, no effect, no skip.
            for case, url, body in (
                ("a refusal the view handled", f"/te-test/scoped/{other.organism_id}/", {"refused": "service"}),
                ("a marked request the view did not confirm", scoped + "?outcome=failed", {"refused": "view"}),
                ("a later confirmed request that touches no LO", "/te-test/unscoped/", {"unscoped": True}),
            ):
                with self.subTest(nothing=case):
                    self.assertEqual(client.get(url).json(), body)
                    self.assertEqual(facts(), (untouched, untouched), "TE-01 nothing: " + case + ".")
            with self.subTest(nothing="a session the landed gate turns away"):
                stale = _signed_in(holder)
                session = stale.session
                session[SESSION_ACCESS_EPOCH_KEY] = holder.access_epoch + 1
                session.save()
                self.assertEqual(stale.get(scoped).status_code, 302)
                self.assertEqual(facts(), (untouched, untouched),
                                 "TE-01 nothing: an identity the session gate refuses never reaches the wake.")

            # The opening. Inside the request nothing has opened; after its close exactly one item has.
            self.assertEqual(client.get(scoped).json(), {"inside": untouched},
                             "TE-01 opening: nothing may open while the request is being answered.")
            self.assertEqual(facts(), ([1, 1, 1, 0], untouched),
                             "TE-01 opening: after the response closes, exactly one of the LO's two due items "
                             "is open, and the other LO's due item is untouched.")
            self.assertEqual(openings(), [(str(treasury.assignment_uuid), "OPENED", founder.pk, treasury_end)],
                             "TE-01 opening: the earlier-due item, entered by the member whose request it was.")

            # The same on a server's order: Django's own close and connection cleanup run first.
            # The engine checks one consumer per wake, so the other consumers take their idle turns first.
            fx.idle_until_turn_of(lo, founder, "ELECT")
            (receipts, ends, probes, skips), _other = facts()
            self.assertEqual((receipts, ends, skips), (1, 1, 0), "TE-01: an idle turn of another consumer opens nothing.")
            cookies = "; ".join(f"{name}={morsel.value}" for name, morsel in client.cookies.items())
            environ = RequestFactory().get(scoped, HTTP_COOKIE=cookies).environ
            response = WSGIHandler()(environ, lambda status, headers, exc_info=None: None)
            self.assertEqual(json.loads(b"".join(response)), {"inside": [1, 1, probes, 0]})
            self.assertEqual(facts(), ([1, 1, probes, 0], untouched),
                             "TE-01 server order: a response that has been produced but not closed has woken nothing.")
            response.close()
            self.assertEqual(facts(), ([2, 2, probes + 1, 0], untouched),
                             "TE-01 server order: the close opens the next due item, once.")
            response.close()
            self.assertEqual(facts(), ([2, 2, probes + 1, 0], untouched), "TE-01 server order: a second close does nothing.")
            self.assertEqual([row[0] for row in openings()], [str(treasury.assignment_uuid), str(secretary.assignment_uuid)])

        # An attempt that finds its connection inside a transaction does nothing there, and says so.
        with transaction.atomic():
            held = te.run(_wake(lo, founder), alias="default")
        self.assertEqual((held.result, facts()), ("IN_TRANSACTION", ([2, 2, probes + 1, 1], untouched)),
                         "TE-01 in a transaction: the attempt must do nothing and be counted against the LO.")

    def test_backlog_progress(self):
        """TE-03. Each wake checks one consumer and opens at most one item, every consumer makes progress, and a failing item does not hold back the rest or lose its due time.

        One LO has a due Board term end (ELECT), two CQ threads past their preparation
        deadline, and a third past its Clarity response deadline. Cases: a consumer whose
        selection fails is recorded and passed, so the next consumer runs; a CQ attempt that
        finds the LO's founding basis held elsewhere ends at once with nothing; a CQ act that
        the landed deferred completeness check refuses at commit leaves no act and no
        receipt; its valid siblings then open, one of each purpose; the refused occurrence
        opens later with its original due time; a late repair of an old failure does not
        disturb newer progress; of two items due at the same instant, one that fails does
        not hold back the other; the CQ service's entry for rule acts refuses any other act.
        """
        lo, founder, _holder, scope, ((office, office_end),) = _ended_terms(("Treasurer",))
        enabled = self.configure(scope, {"enabled": True})
        threads = []
        for title in ("Review the opening hours", "Review the closing hours"):
            journey, _category, post = self.topic(scope, title=title)
            with clarity.due_at_phase_start():
                cq.put_forward(**journey, version_uuid=post["version"], request_uuid=uuid4())
            threads.append((journey, cq.phase(**journey)))
        # A third thread is prepared, so its head is a Clarity window; its response deadline has passed.
        third, _category, third_post = self.topic(scope, title="Review the holiday hours")
        cq.put_forward(**third, version_uuid=third_post["version"], request_uuid=uuid4())
        with clarity.due_at_phase_start():
            window = cq.mark_prepared(**third, request_uuid=uuid4())
        third_phase = cq.phase(**third)
        self.assertEqual(third_phase["phase"], "CLARITY")
        self.configure(scope, {"enabled": False, "clarity_days": 3}, enabled)
        (first, first_phase), (second, second_phase) = threads
        self.assertLess(first_phase["deadline"], second_phase["deadline"])
        self.assertLess(second_phase["deadline"], third_phase["response_deadline"])

        def wake():
            return te.run(_wake(lo, founder), alias="default")

        def pace():
            return {row[0]: row[1:] for row in _rows(
                """SELECT consumer, probes, opened, failed, last_failure_class, last_failure_code
                FROM public.core_tepace WHERE organism_id=%s""", [lo.pk])}

        def due_acts(journey):
            return _rows("SELECT act_uuid, actor_id FROM public.core_cqact WHERE thread_uuid=%s AND operation='PREPARATION_DUE'",
                         [journey["thread_uuid"]])

        def receipts():
            return _rows("""SELECT consumer, purpose, source_ref, outcome, due_at FROM public.core_tereceipt
                WHERE organism_id=%s ORDER BY discovered_at""", [lo.pk])

        # 1. The first consumer's selection fails. It is recorded as probed and failed, with no text kept.
        with mock.patch.object(te, "_select", side_effect=RuntimeError("TE-03 injected selector failure")):
            failed = wake()
        self.assertEqual((failed.result, failed.consumer, failed.code), ("ERROR", "ELECT", "INTERNAL"))
        self.assertEqual(pace(), {"ELECT": (1, 0, 1, "ERROR", "INTERNAL")},
                         "TE-03 failing selector: the consumer whose selection failed must be recorded as probed.")

        # 1a. The next consumer is CQ. The LO's founding basis, which a rule act's event on the LO's
        # chain cites, is held by another connection, as the landed authority lifecycle holds one
        # (0056). The event's foreign key would wait for it at commit, with the LO held. CQ's claim
        # tries it first, so the attempt ends at once: no act, no receipt, no consumer charged.
        basis = _rows("SELECT founding_authority_basis_id FROM public.core_livingorganism WHERE id=%s", [lo.pk])[0][0]
        held, let_go = threading.Event(), threading.Event()

        def hold_basis():
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM public.core_authoritybasis WHERE id=%s FOR UPDATE", [basis])
                    held.set()
                    let_go.wait(WAIT)
            finally:
                connections.close_all()

        holder_thread = threading.Thread(target=hold_basis)
        holder_thread.start()
        try:
            self.assertTrue(held.wait(WAIT), "TE-03 held basis: the other connection never took the authority basis.")
            skipped = wake()
            self.assertTrue(holder_thread.is_alive(), "TE-03 held basis: the engine must return while the basis is still held.")
            self.assertEqual((skipped.result, skipped.consumer), ("BUSY_INNER", None),
                             "TE-03 held basis: with the LO's founding basis held elsewhere a CQ attempt must skip at once.")
            self.assertEqual((_rows("SELECT count(*) FROM public.core_cqact WHERE organism_id=%s AND operation IN "
                                    "('PREPARATION_DUE','RESPONSE_DUE')", [lo.pk]), receipts(), pace(),
                              _rows("SELECT busy_lo, busy_inner FROM public.core_temeter WHERE organism_id=%s", [lo.pk])),
                             ([(0,)], [], {"ELECT": (1, 0, 1, "ERROR", "INTERNAL")}, [(0, 1)]),
                             "TE-03 held basis: the skip must leave no rule act and no receipt, be counted as a busy "
                             "inner row, and be charged to no consumer.")
        finally:
            let_go.set()
            holder_thread.join(WAIT)
        self.assertFalse(holder_thread.is_alive(), "TE-03 held basis: the other connection did not finish.")

        # 2. So the next consumer runs. Its first item is refused at commit by the landed deferred
        # completeness check: by then the act's enterer is not an active credential. The test brings
        # that about inside the attempt's own transaction, which the refused commit then undoes.
        def enterer_inactive_by_commit(_opening):
            _rows("UPDATE auth_user SET is_active=false WHERE id=%s RETURNING id", [founder.credential_id])

        with mock.patch.object(te, "_between_effect_and_receipt", side_effect=enterer_inactive_by_commit):
            refused = wake()
        self.assertEqual((refused.result, refused.consumer, refused.purpose, refused.source_ref, refused.code),
                         ("COMMIT_FAILED", "CQ", "PREPARATION_DUE", str(first["thread_uuid"]), "CQ_COMPLETE"),
                         "TE-03 deferred refusal: the next consumer must run, and its act must be refused at commit.")
        self.assertEqual((due_acts(first), receipts(), cq.phase(**first)["deadline"]),
                         ([], [], first_phase["deadline"]),
                         "TE-03 deferred refusal: the act and its receipt must both be gone, and the head unchanged.")
        self.assertTrue(models.Identity.objects.get(pk=founder.pk).credential.is_active)
        self.assertEqual(pace(), {"ELECT": (1, 0, 1, "ERROR", "INTERNAL"), "CQ": (1, 0, 1, "COMMIT_FAILED", "CQ_COMPLETE")},
                         "TE-03 deferred refusal: the lost attempt must still be recorded as a probe of its consumer.")

        # 3. One consumer per wake, in turn; at most one opening per wake; the valid sibling is not held back.
        # Any other registered consumer has nothing due in this LO. Its turns come round in between,
        # must be idle, and are passed over here, so the case stays correct as consumers are admitted.
        # C2 is one of them: the office ends above are coverage boundaries too. The landed C2 command
        # notices them here, as on main, so C2 owes nothing in this rotation. C2-06 is C2's own witness.
        organism_recovery.advance("default", lo.pk)
        steps = []
        for _ in range(16):
            step = wake()
            if step.consumer in ("ELECT", "CQ"):
                steps.append(step)
            else:
                self.assertEqual(step.result, "IDLE", f"TE-03 rotation: {step.consumer} has nothing due in this LO.")
            if len(steps) == 6:
                break
        self.assertEqual([(step.result, step.consumer, step.purpose, step.source_ref) for step in steps], [
            ("OPENED", "ELECT", "TERM_END", str(office.assignment_uuid)),
            ("OPENED", "CQ", "PREPARATION_DUE", str(second["thread_uuid"])),
            ("IDLE", "ELECT", None, None),
            ("OPENED", "CQ", "RESPONSE_DUE", str(third["thread_uuid"])),
            ("IDLE", "ELECT", None, None),
            ("OPENED", "CQ", "PREPARATION_DUE", str(first["thread_uuid"])),
        ], "TE-03 rotation: the consumers must take turns, and the refused thread's siblings must open before it.")
        paced = pace()
        self.assertEqual({name: paced[name] for name in ("ELECT", "CQ")},
                         {"ELECT": (4, 1, 1, "ERROR", "INTERNAL"), "CQ": (4, 3, 1, "COMMIT_FAILED", "CQ_COMPLETE")})
        self.assertEqual({row[1:3] for name, row in paced.items() if name not in ("ELECT", "CQ")} - {(0, 0)}, set(),
                         "TE-03 rotation: a consumer with nothing due must have opened nothing and failed nothing.")
        self.assertEqual(receipts(), [
            ("ELECT", "TERM_END", str(office.assignment_uuid), "OPENED", office_end),
            ("CQ", "PREPARATION_DUE", str(second["thread_uuid"]), "OPENED", second_phase["deadline"]),
            ("CQ", "RESPONSE_DUE", str(third["thread_uuid"]), "OPENED", third_phase["response_deadline"]),
            ("CQ", "PREPARATION_DUE", str(first["thread_uuid"]), "OPENED", first_phase["deadline"]),
        ], "TE-03 due time: the occurrence that was refused must open later with its original due time.")
        # The response deadline's act is the landed rule act for that window, entered by the waking member.
        self.assertEqual(
            _rows("""SELECT a.actor_id, a.authority_source->>'kind', a.authority_source->>'reference', a.request->>'window'
                FROM public.core_cqact a WHERE a.thread_uuid=%s AND a.operation='RESPONSE_DUE'""", [third["thread_uuid"]]),
            [(founder.pk, "RULE", "HG:DISC2:D7", str(window))],
            "TE-03 response deadline: exactly one rule act, for that window, made as the landed catch-up makes it.")
        self.assertNotEqual(cq.phase(**third)["phase"], "CLARITY",
                            "TE-03 response deadline: the thread must have moved on from its Clarity window.")
        self.assertEqual(([row[1] for row in due_acts(first)], [row[1] for row in due_acts(second)]),
                         ([founder.pk], [founder.pk]))

        # 4. A repair that arrives late, for a failure older than the progress since, changes nothing.
        before = pace()
        stale = {"consumer": "CQ", "probes": 1, "cursor_due": None, "cursor_purpose": None,
                 "cursor_source": None, "cursor_occurrence": None}
        te._count_lost_attempt("default", lo.organism_id, lo.pk, stale, "COMMIT_FAILED", "CQ_COMPLETE", None)
        self.assertEqual(pace(), before, "TE-03 late repair: a newer probe must never be overwritten.")
        self.assertEqual(cq.catch_up(credential=founder.credential), "Nothing is due.")

        # 5. Equal due times. In a second LO two Board offices reach their fixed end at one and the same
        # instant, and the first attempt on one of them fails. The other must not wait behind it: the
        # next ELECT turn opens the sibling, and the turn after that opens the one that failed.
        twin_lo, twin_founder, _twin_holder, _twin_scope, twins = _ended_terms(("Treasurer", "Secretary"), shared_end=True)
        (_, twin_end), (_, other_end) = twins
        self.assertEqual(twin_end, other_end, "TE-03 fixture: the two terms must end at the same instant.")

        def twin_turn():
            fx.idle_until_turn_of(twin_lo, twin_founder, "ELECT")
            return te.run(_wake(twin_lo, twin_founder), alias="default")

        with mock.patch.object(te, "_between_effect_and_receipt", side_effect=RuntimeError("TE-03 injected failure")):
            stumbled = twin_turn()
        self.assertEqual((stumbled.result, stumbled.purpose), ("FAILED", "TERM_END"),
                         "TE-03 equal due times: the injected failure must be recorded against one of the two terms.")
        opened = [twin_turn(), twin_turn()]
        self.assertEqual([(step.result, step.purpose) for step in opened], [("OPENED", "TERM_END")] * 2,
                         "TE-03 equal due times: each turn must open one of the two terms.")
        self.assertEqual({step.source_ref for step in opened}, {str(assignment.assignment_uuid) for assignment, _ in twins},
                         "TE-03 equal due times: both terms must be opened.")
        self.assertEqual(opened[1].source_ref, stumbled.source_ref,
                         "TE-03 equal due times: an item due at the same instant as one that failed must open before the retry.")
        self.assertEqual(_rows("SELECT due_at FROM public.core_tereceipt WHERE organism_id=%s ORDER BY discovered_at", [twin_lo.pk]),
                         [(twin_end,), (twin_end,)], "TE-03 equal due times: both receipts must hold the one due time.")
        self.assertEqual(twin_turn().result, "IDLE")

        # 6. The CQ service's entry for rule acts admits those acts only, on the connection it is given.
        ((third_board,),) = _rows("SELECT board_uuid FROM public.cq_phases WHERE thread_uuid=%s", [third["thread_uuid"]])
        entry = {"actor": founder, "organism_uuid": lo.organism_id, "board_uuid": third_board,
                 "thread_uuid": third["thread_uuid"], "key": uuid4(), "args": {}, "received": support.database_now()}
        with self.assertRaises(ValueError, msg="TE-03 rule-act entry: a member's own act is not a rule act."):
            cq.record_rule_act(alias="default", operation="WITHDRAW", **entry)
        with self.assertRaises(ConnectionDoesNotExist, msg="TE-03 rule-act entry: the act must be made on the "
                               "connection named, not on the default one."):
            cq.record_rule_act(alias="te03-no-such-connection", operation="PREPARATION_DUE", **entry)
        self.assertEqual(_rows("SELECT count(*) FROM public.core_cqact WHERE thread_uuid=%s AND act_uuid=%s",
                               [third["thread_uuid"], entry["key"]]), [(0,)])

    def test_made_here_is_the_writing_transaction_and_no_other(self):
        """TE-07. An effect is "made here" only for the top-level transaction that wrote it, whichever savepoint of it wrote it, and for no other transaction, earlier, later or concurrent.

        The effect is a C2 coverage notice made by the landed writer. Cases: a write in a
        savepoint that is rolled back leaves no effect and no origin; a write at the top level
        is made here, and after commit is history, which can only be found done; a write in a
        released savepoint is made here, and its commit passes the landed recovery guard; another
        session's write is unseen while uncommitted and is not made here once committed; a full
        transaction id that differs from this one only above its low 32 bits is not this
        transaction; an origin cannot be written directly, changed or removed; and a nested
        writer that is not the effect's own insert, a trigger on some other table, cannot
        write an origin for an effect that is already there, whether it has an origin of its
        own, is of a kind whose origin is not recorded, or predates the record (a coverage
        assessment made while the recorder was off, as every one on main was); nor for a relation that
        is not an approved source; nor keep one for an effect that does not come to exist by
        commit. A transaction that has asked for the origin check at once still makes its notice.
        """
        made_here = (True, True)
        history = (False, False)

        with self.subTest(case="a rolled-back savepoint"):
            lo, source, occurrence = _owing_a_notice()
            with transaction.atomic():
                try:
                    with transaction.atomic():  # a savepoint
                        self.assertTrue(_notice(lo, source))
                        inside = _provenance(lo, source, occurrence)
                        key, = _rows("""SELECT o.effect_key FROM public.core_teorigin o JOIN public.core_coverageassessment a
                            ON a.event_uuid::text=o.effect_key AND o.effect_table='core_coverageassessment'
                            WHERE a.evidence_reference='c2-boundary:'||%s""", [source])[0]
                        raise _Undo()
                except _Undo:
                    pass
                self.assertEqual(inside, made_here, "TE-07: inside its own savepoint the write is made here")
                self.assertEqual((_provenance(lo, source, occurrence),
                                  _rows("SELECT count(*) FROM public.core_teorigin WHERE effect_key=%s", [key])[0][0],
                                  _rows("SELECT public.te_made_here('core_coverageassessment',%s)", [key])[0][0]),
                                 (None, 0, False),
                                 "TE-07: a rolled-back savepoint leaves no effect, no origin, and nothing made here")

        with self.subTest(case="the top level"):
            lo, source, occurrence = _owing_a_notice()
            with transaction.atomic():
                self.assertTrue(_notice(lo, source))
                self.assertEqual(_provenance(lo, source, occurrence), made_here, "TE-07: a write at the top level is made here")
            self.assertEqual(_provenance(lo, source, occurrence), history,
                             "TE-07: after its commit an effect is history: it can be found done, never made here")

        with self.subTest(case="a released savepoint"):
            lo, source, occurrence = _owing_a_notice()
            with transaction.atomic():
                with transaction.atomic():  # a savepoint, released
                    self.assertTrue(_notice(lo, source))
                self.assertEqual(_provenance(lo, source, occurrence), made_here,
                                 "TE-07: a write in a released savepoint is made by this transaction")
            self.assertEqual((_provenance(lo, source, occurrence),
                              models.LivingOrganismEvent.objects.filter(living_organism_id=lo, action="WITHOUT_COORDINATOR").count()),
                             (history, 1),
                             "TE-07: the commit passes the landed recovery guard, and the effect is then history")

        with self.subTest(case="another session"):
            lo, source, occurrence = _owing_a_notice()
            written, release, problems, theirs = threading.Event(), threading.Event(), [], []

            def elsewhere():
                try:
                    with transaction.atomic():
                        _notice(lo, source)
                        theirs.append(_rows("""SELECT a.event_uuid::text FROM public.core_coverageassessment a
                            WHERE a.evidence_reference='c2-boundary:'||%s""", [source])[0][0])
                        written.set()
                        release.wait(WAIT)
                except Exception as exc:  # noqa: BLE001 - reported by the assertion below
                    problems.append(type(exc).__name__)
                finally:
                    written.set()
                    connection.close()

            other = threading.Thread(target=elsewhere)
            other.start()
            try:
                self.assertTrue(written.wait(WAIT), "TE-07: the other session must write")
                with transaction.atomic():
                    unseen = (_provenance(lo, source, occurrence),
                              _rows("SELECT public.te_made_here('core_coverageassessment',%s)", theirs[:1] or [""])[0][0])
            finally:
                release.set()
                other.join(WAIT)
            self.assertEqual((problems, len(theirs), unseen), ([], 1, (None, False)),
                             "TE-07: another session's uncommitted write is not seen, and its effect is not made here")
            with transaction.atomic():
                self.assertEqual(_provenance(lo, source, occurrence), history,
                                 "TE-07: another session's committed write is not made here")

        with self.subTest(case="equal low 32 bits"):
            self.assertEqual(_rows("""SELECT public.te_same_transaction(x.own), public.te_same_transaction(x.far), x.far::xid=x.own::xid
                FROM (SELECT pg_current_xact_id() AS own,
                             ((pg_current_xact_id()::text::numeric+4294967296)::text)::xid8 AS far) x""")[0],
                             (True, False, True),
                             "TE-07: an id equal to this transaction's in its low 32 bits only is another transaction")

        with self.subTest(case="the origin record is the database's own"):
            lo, source, occurrence = _owing_a_notice()
            with transaction.atomic():
                _notice(lo, source)
            for statement in ("INSERT INTO public.core_teorigin(effect_table,effect_key) VALUES ('core_coverageassessment','forged')",
                              "INSERT INTO public.core_teorigin(effect_table,effect_key,top_xid) VALUES ('core_coverageassessment','forged',pg_current_xact_id())",
                              "UPDATE public.core_teorigin SET top_xid=pg_current_xact_id()",
                              "DELETE FROM public.core_teorigin"):
                with self.assertRaises(IntegrityError, msg="TE-07: must be refused: " + statement[:6]):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(statement)
            self.assertEqual(_provenance(lo, source, occurrence), history, "TE-07: the refused statements changed nothing")

        with self.subTest(case="an unrelated nested writer"):
            lo, source, occurrence = _owing_a_notice()
            with transaction.atomic():
                _notice(lo, source)
            (noticed,), = _rows("""SELECT a.event_uuid::text FROM public.core_coverageassessment a
                WHERE a.evidence_reference='c2-boundary:'||%s""", [source])
            (unrecorded,), = _rows("""SELECT e.event_uuid::text FROM public.core_livingorganismevent e
                WHERE NOT EXISTS(SELECT FROM public.core_teorigin o WHERE o.effect_table='core_livingorganismevent'
                    AND o.effect_key=e.event_uuid::text) ORDER BY e.id DESC LIMIT 1""")
            self.assertEqual(_rows("""SELECT public.te_effect_exists('core_livingorganismevent',%s,false),
                    public.te_effect_exists('core_livingorganismevent',%s,true)""", [unrecorded, unrecorded]), [(True, False)],
                "TE-07 fixture: an LO event that exists, has no origin, and is of a kind whose origin is not recorded")
            absent = str(uuid4())
            # An effect of a recorded kind with no origin, as every one made before 0092 is: the
            # founding coverage assessments of a second LO, founded with the recorder off.
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.core_coverageassessment DISABLE TRIGGER zz_te_origin")
            try:
                old_lo, _old_source, _old_occurrence = _owing_a_notice()
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("ALTER TABLE public.core_coverageassessment ENABLE TRIGGER zz_te_origin")
            (predating,), = _rows("""SELECT a.event_uuid::text FROM public.core_coverageassessment a
                JOIN public.core_essentialcoveragecase c ON c.id=a.case_id WHERE c.living_organism_id=%s
                ORDER BY a.id DESC LIMIT 1""", [old_lo])
            self.assertEqual((_rows("SELECT count(*) FROM public.core_teorigin WHERE effect_key=%s", [predating]),
                              _rows("SELECT public.te_effect_exists('core_coverageassessment',%s,true)", [predating]),
                              _rows("""SELECT tgenabled::text FROM pg_trigger WHERE tgname='zz_te_origin'
                                  AND tgrelid='public.core_coverageassessment'::regclass""")),
                             ([(0,)], [(True,)], [("O",)]),
                             "TE-07 fixture: an assessment of a recorded kind with no origin, and the recorder on again")

            def origins(key):
                return _rows("SELECT count(*), bool_or(public.te_made_here(o.effect_table,o.effect_key)) "
                             "FROM public.core_teorigin o WHERE o.effect_key=%s", [key])[0]

            def nested(relation, key):
                # Runs to its commit: the origin is written by the other table's trigger, at depth 2.
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("INSERT INTO te07_other(relation,key) VALUES (%s,%s)", [relation, key])

            with connection.cursor() as cursor:
                cursor.execute("CREATE TEMP TABLE te07_other(relation text, key text)")
                cursor.execute("""CREATE FUNCTION pg_temp.te07_claim() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN
                     INSERT INTO public.core_teorigin(effect_table,effect_key) VALUES (NEW.relation,NEW.key);
                     RETURN NEW;
                    END $$""")
                cursor.execute("CREATE TRIGGER te07_claim BEFORE INSERT ON te07_other "
                               "FOR EACH ROW EXECUTE FUNCTION pg_temp.te07_claim()")
            try:
                for relation, key, refusal, why in (
                        ("core_coverageassessment", predating, "TE_ORIGIN_EXISTS", "an existing effect with no origin"),
                        ("core_livingorganismevent", unrecorded, "TE_ORIGIN_EXISTS", "an existing effect of a kind not recorded"),
                        ("core_coverageassessment", noticed, "TE_ORIGIN_EXISTS", "an existing effect with an origin"),
                        ("core_tereceipt", absent, "TE_ORIGIN_SOURCE", "a relation that is not an approved source"),
                        ("core_coverageassessment", absent, "TE_ORIGIN_ORPHAN", "an effect that never comes to exist")):
                    before = origins(key)
                    with self.assertRaises(IntegrityError, msg="TE-07: a nested writer's origin must be refused for " + why) as refused:
                        nested(relation, key)
                    self.assertIn(refusal, str(refused.exception), "TE-07: refused on the wrong ground for " + why)
                    self.assertEqual(origins(key), before, "TE-07: the refused origin must not be kept for " + why)
                self.assertEqual((origins(predating), origins(unrecorded), origins(absent), origins(noticed)),
                                 ((0, None), (0, None), (0, None), (1, False)),
                                 "TE-07: no origin for the effects that had none, none for the absent one, the notice's own unchanged")
                self.assertEqual(_rows("SELECT public.te_made_here('core_coverageassessment',%s)", [predating]), [(False,)],
                                 "TE-07: the effect that predates the record is never made here")
                self.assertEqual(_rows("SELECT count(*) FROM te07_other"), [(0,)], "TE-07: no nested write was kept")
                self.assertEqual(_provenance(lo, source, occurrence), history, "TE-07: the notice is still history, never made here")

                # The effect's own insert is not refused when the transaction has asked for the origin
                # check at once. (All constraints at once is no test of it: the landed recovery guard
                # is itself deferred, and refuses the notice before its commit.)
                now_lo, now_source, now_occurrence = _owing_a_notice()
                try:
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute("SET CONSTRAINTS public.te_origin_complete IMMEDIATE")
                        self.assertTrue(_notice(now_lo, now_source))
                        self.assertEqual(_provenance(now_lo, now_source, now_occurrence), made_here,
                                         "TE-07: with the origin check asked for at once, the write is made here")
                except IntegrityError as refused:
                    self.fail("TE-07: with the origin check asked for at once, the effect's own origin was refused: " + str(refused)[:40])
                self.assertEqual(_provenance(now_lo, now_source, now_occurrence), history,
                                 "TE-07: and after its commit it is history")
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("DROP TABLE te07_other")
                    cursor.execute("DROP FUNCTION pg_temp.te07_claim()")

    def test_measures_are_read_by_the_technology_circle_and_nobody_else(self):
        """TE-04. The engine's measures are read across LOs by a current member of INTEVIA's Technology Circle, and by nobody else.

        Cases: the root's founder, who holds a place in the Technology Circle the root's own
        birth batch names, reads counts for an ordinary LO's opening; nothing in what is
        returned names an LO, a source or an effect; a member of the root with no place in
        that Circle, and a member of an ordinary LO, each get the one unavailable answer; so
        does the place holder once their credential is inactive or their identity restricted,
        and a credential with no identity: the answer never tells one reason from another.
        """
        from src.intevia.services import circle_participation as places
        from src.intevia.services import root_circles
        from src.intevia.services import root_genesis as genesis
        from src.intevia.services import timed_events_measures as measures
        from tests import root_genesis_support

        root = genesis.execute(root_genesis_support.command())
        steward = models.RootGenesis.objects.get(organism_uuid=root.organism_id).founder
        root_genesis_support.prepare_root_beta(root, steward)
        batch = root_circles.birth_inventory(credential=steward.credential, organism_uuid=root.organism_id,
                                             request_uuid=uuid4())
        (technology,) = [entry["circle_uuid"] for entry in batch.circles if entry["name"] == "INTEVIA Technology"]
        self.assertTrue(models.CircleParticipation.objects.filter(
            circle__circle_uuid=technology, membership__identity=steward).exists())

        # One real opening in an ordinary LO, so there is something to add up.
        lo, founder, _treasurer, _scope, assignment, _office_end = _ended_treasury_term()
        fx.idle_until_turn_of(lo, founder, "ELECT")
        opened = te.run(_wake(lo, founder), alias="default")
        self.assertEqual((opened.result, opened.consumer, opened.purpose), ("OPENED", "ELECT", "TERM_END"))

        read = measures.read(credential=steward.credential)
        self.assertEqual(sorted(read), ["consumers", "pace_rows_by_last_failure", "receipts", "sampled_at", "skips_lower_bound"])
        (elect,) = [row for row in read["consumers"] if row["consumer"] == "ELECT"]
        self.assertEqual((elect["organisms"], elect["opened"], elect["found_done"], elect["blocked"], elect["failed"]),
                         (1, 1, 0, 0, 0), "TE-04: the one opening is counted once, under its consumer")
        self.assertGreaterEqual(elect["probes"], 1)
        self.assertIsNotNone(elect["max_ms"])
        (receipt,) = read["receipts"]
        self.assertEqual((receipt["consumer"], receipt["purpose"], receipt["outcome"], receipt["receipts"]),
                         ("ELECT", "TERM_END", "OPENED", 1))
        self.assertGreaterEqual(receipt["lateness_max"], timedelta(0), "TE-04: lateness is the effect's time less its due time")
        self.assertEqual(set(read["skips_lower_bound"]),
                         {"busy_lo", "busy_inner", "in_transaction", "not_eligible", "no_limits", "commit_failed", "errors"})
        told = repr(read)
        for private in (str(lo.organism_id), str(assignment.assignment_uuid), str(root.organism_id)):
            self.assertNotIn(private, told, "TE-04: the read names no LO, no source and no effect")
        keys = {key for row in read["consumers"] + read["receipts"] + read["pace_rows_by_last_failure"] for key in row}
        self.assertFalse(keys & {"organism_id", "source_ref", "effect_ref", "occurrence", "enterer_id"},
                         "TE-04: no column that could open an LO's records is returned")

        unavailable = "This request cannot use the selected Circle or membership. Check the organisation and selection."
        outside_circle = law.add_member(root)
        for person, why in ((outside_circle, "a member of the root with no place in the Technology Circle"),
                            (founder, "a member of an ordinary LO")):
            with self.assertRaises(places.CircleAccessRefused, msg=f"TE-04: {why} must be refused") as refused:
                measures.read(credential=person.credential)
            self.assertEqual(str(refused.exception), unavailable, f"TE-04: {why} gets the one unavailable answer")

        # The place holder, refused by the landed actor check before any place is looked at, and a
        # credential nobody holds: each reason has its own landed wording, and none of it may leave the read.
        from django.contrib.auth import get_user_model

        users = get_user_model().objects
        nobody = users.create_user(username="nobody-" + uuid4().hex)
        identities = models.Identity.objects.filter(pk=steward.pk)

        def refusal(credential):
            with self.assertRaises(places.CircleAccessRefused) as refused:
                measures.read(credential=credential)
            return str(refused.exception), refused.exception.__cause__, refused.exception.__context__

        users.filter(pk=steward.credential_id).update(is_active=False)
        inactive = refusal(steward.credential)
        users.filter(pk=steward.credential_id).update(is_active=True)
        identities.update(access_state=models.Identity.AccessState.RESTRICTED)
        restricted = refusal(steward.credential)
        identities.update(access_state=models.Identity.AccessState.ACTIVE)
        self.assertEqual({"an inactive credential": inactive, "a restricted identity": restricted,
                          "a credential with no identity": refusal(nobody)},
                         dict.fromkeys(("an inactive credential", "a restricted identity", "a credential with no identity"),
                                       (unavailable, None, None)),
                         "TE-04: every refusal of access is the one unavailable answer, with no other refusal attached")
        self.assertEqual(sorted(measures.read(credential=steward.credential)), sorted(read),
                         "TE-04: the place holder, active again, reads as before")

    def test_limits_are_one_fixed_version(self):
        """TE-05. The engine runs under one fixed version of its limits, and storage accepts no other.

        Cases: the version in force is exactly the ruled one and is the one the runner
        supports; a second version, a changed limit, and a version under another rule id
        are each refused by storage by name; the version in force is unchanged.
        """
        def in_force():
            return _rows("SELECT public.te_limits_at(statement_timestamp())")[0][0]

        def record(**changed):
            """Offer storage a copy of the limits version with the given columns changed."""
            row = json.loads(_rows("SELECT to_jsonb(r)::text FROM public.core_governedrule r WHERE r.code='TIMED_EVENTS_LIMITS'")[0][0])
            # The id comes from the table's own sequence; storage assigns the record time and the commitment itself.
            row.update(id=_rows("SELECT nextval(pg_get_serial_sequence('public.core_governedrule','id'))")[0][0],
                       recorded_at=None, text_commitment=None, **changed)
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("""INSERT INTO public.core_governedrule
                    SELECT * FROM jsonb_populate_record(NULL::public.core_governedrule, %s::jsonb)""", [json.dumps(row)])

        ruled = in_force()
        ruled = json.loads(ruled) if isinstance(ruled, str) else ruled
        self.assertEqual(ruled, te.SUPPORTED_LIMITS, "TE-05: the version in force is the ruled one, and the runner supports it")
        self.assertEqual(_rows("SELECT count(*) FROM public.core_governedrule WHERE code='TIMED_EVENTS_LIMITS'")[0][0], 1)
        faster = dict(ruled, items_per_request=5)
        for changed, why in (({"version": 2}, "a second version"),
                             ({"version": 2, "body": faster}, "a second version with a changed limit"),
                             ({"rule_uuid": str(uuid4())}, "a version under another rule id"),
                             ({"body": faster, "rule_uuid": str(uuid4())}, "a changed limit under another rule id")):
            with self.assertRaises(IntegrityError, msg=f"TE-05: {why} must be refused") as refused:
                record(**changed)
            self.assertIn("TE_GOVERNEDRULE_LIMITS_INVALID", str(refused.exception), f"TE-05: {why} is refused by the limits rule itself")
        after = in_force()
        self.assertEqual(json.loads(after) if isinstance(after, str) else after, ruled, "TE-05: the version in force is unchanged")
        self.assertEqual(_rows("SELECT count(*) FROM public.core_governedrule WHERE code='TIMED_EVENTS_LIMITS'")[0][0], 1)

    def test_an_attempt_that_may_not_act_or_is_refused_by_name_leaves_nothing(self):
        """TE-06. An attempt whose carrier is no longer the person who made the request, or whose act the writer refuses by name, leaves no effect and no receipt, and is recorded by its class.

        Cases: a wake carrying an access epoch that is no longer the person's ends as not
        eligible, before any consumer is probed, and is counted as a skip against the LO; a
        refusal the writer is known to give is reported and kept as that domain refusal, by
        its code, with the probe; the occurrence is then opened by the next clean attempt; an
        occurrence the landed route completed after it was selected is found done, with one
        receipt that cites the landed effect and its earlier time, and is counted as found done.
        """
        lo, founder, _treasurer, _scope, assignment, _office_end = _ended_treasury_term()
        ends = models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment, action="END")

        def pace():
            return _rows("""SELECT consumer, probes, opened, failed, last_failure_class, last_failure_code
                FROM public.core_tepace WHERE organism_id=%s ORDER BY consumer""", [lo.pk])

        def skipped():
            return _rows("SELECT not_eligible, last_skip_class FROM public.core_temeter WHERE organism_id=%s", [lo.pk])

        # 1. The person's access epoch has moved on since the request was made.
        stale = replace(_wake(lo, founder), access_epoch=founder.access_epoch + 1)
        turned_away = te.run(stale, alias="default")
        self.assertEqual((turned_away.result, turned_away.consumer), ("NOT_ELIGIBLE", None),
                         "TE-06: a wake that is no longer this person's must not act")
        self.assertEqual((ends.count(), pace(), skipped()), (0, [], [(1, "NOT_ELIGIBLE")]),
                         "TE-06: nothing is opened or probed, and the skip is counted against the LO")

        # 2. The writer refuses by a name it is known to give.
        def known_refusal(_opening):
            _rows("DO $$ BEGIN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END $$")

        fx.idle_until_turn_of(lo, founder, "ELECT")
        before = pace()
        with mock.patch.object(te, "_between_effect_and_receipt", side_effect=known_refusal):
            refused = te.run(_wake(lo, founder), alias="default")
        self.assertEqual((refused.result, refused.consumer, refused.purpose, refused.failure_class, refused.code),
                         ("FAILED", "ELECT", "TERM_END", "DOMAIN_REFUSED", "DISC_UNAVAILABLE"),
                         "TE-06: a known refusal is reported as that domain refusal, by its code")
        self.assertEqual(ends.count(), 0, "TE-06: the refused act leaves no term end")
        self.assertEqual(_rows("SELECT count(*) FROM public.core_tereceipt WHERE organism_id=%s", [lo.pk])[0][0], 0)
        self.assertEqual([row for row in pace() if row[0] == "ELECT"], [("ELECT", 1, 0, 1, "DOMAIN_REFUSED", "DISC_UNAVAILABLE")],
                         "TE-06: the probe and the refusal's class and code are kept")
        self.assertEqual([row for row in pace() if row[0] != "ELECT"], [row for row in before if row[0] != "ELECT"])

        # 3. The occurrence was not lost: the next clean attempt at its consumer's turn opens it.
        fx.idle_until_turn_of(lo, founder, "ELECT")
        opened = te.run(_wake(lo, founder), alias="default")
        self.assertEqual((opened.result, opened.purpose, ends.count()), ("OPENED", "TERM_END", 1),
                         "TE-06: the refused occurrence opens later, once")

        # 4. Found done. The landed route completes an occurrence after the engine has selected it.
        # In one real attempt that cannot happen: the landed writer takes the LO lock the attempt
        # holds from its selection to its commit. So the selection is taken first, the landed route
        # then acts and commits, and the attempt is run with that selection held.
        lo, founder, _treasurer, scope, assignment, office_end = _ended_treasury_term()
        ends = models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=assignment, action="END")
        selected = te._select("default", lo.pk, te._registry("default", "ELECT"), support.database_now(),
                              dict.fromkeys(("cursor_due", "cursor_purpose", "cursor_source", "cursor_occurrence")))
        self.assertEqual((selected["purpose"], selected["source_ref"], selected["due_at"]),
                         ("TERM_END", str(assignment.assignment_uuid), office_end),
                         "TE-06: the engine's own selection is the ended term")
        self.assertEqual(elections.catch_up_terms(**scope), 1, "TE-06: the landed route records the term end")
        landed = ends.get()
        with mock.patch.object(te, "_select", return_value=selected):
            found = te.run(_wake(lo, founder), alias="default")
        self.assertEqual((found.result, found.consumer, found.purpose, found.source_ref, found.failure_class),
                         ("FOUND_DONE", "ELECT", "TERM_END", str(assignment.assignment_uuid), None),
                         "TE-06: an occurrence completed after selection is found done")
        self.assertEqual(ends.count(), 1, "TE-06: finding it done makes no second term end")
        self.assertEqual(_rows("""SELECT outcome, effect_ref, enterer_id, due_at, effect_at, effect_at<discovered_at
            FROM public.core_tereceipt WHERE organism_id=%s""", [lo.pk]),
                         [("FOUND_DONE", str(landed.event_uuid), founder.pk, office_end, landed.recorded_at, True)],
                         "TE-06: one receipt, citing the landed effect and the earlier time it was really made")
        self.assertEqual(_rows("""SELECT consumer, probes, opened, found_done, failed FROM public.core_tepace
            WHERE organism_id=%s""", [lo.pk]), [("ELECT", 1, 0, 1, 0)],
                         "TE-06: the attempt is counted as found done, and as nothing else")
