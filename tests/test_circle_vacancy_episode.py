"""C1-33. The episode has one causal loss and one effective deadline at both knowledge cuts."""

import threading
from datetime import timedelta
from importlib import import_module
from unittest import mock
from uuid import uuid4

from core import models
from django.db import DatabaseError, connection, connections, transaction
from django.test import TransactionTestCase
from intevia.test_postgresql_backend import clock
from src.intevia.services import circle_commands as commands
from src.intevia.services import circle_participation as places
from src.intevia.services import timed_events as te
from tests import election_support
from tests import s015_0021_support as support
from tests import test_circle_operations as circles

migration = import_module("core.migrations.0105_circle_vacancy_episode")
records = import_module("core.migrations.0106_circle_vacancy_records")
committee_cut = import_module("core.migrations.0107_committee_circle_dormancy")
reseat = import_module("core.migrations.0108_circle_reseat")
chair = import_module("core.migrations.0109_committee_chair_reservation")


def episode(circle, at, known):
    with connection.cursor() as cursor:
        cursor.execute("SELECT cause,loss,deadline,status FROM public.circle_episode(%s,%s,%s)",
                       [circle.pk, at, known])
        return cursor.fetchone()


def leave(fixture, circle, place):
    commands.leave(credential=place.membership.identity.credential, organism_uuid=fixture.lo.organism_id,
                   circle_uuid=circle.circle_uuid, participation_uuid=place.participation_uuid,
                   request_uuid=uuid4(), confirmed=True)
    return models.ContextualRoleAssignmentStateEvent.objects.get(
        assignment__circle=circle, assignment__membership=place.membership, action="END")


def form_with_members():
    fixture = circles.CircleOperationsTests()
    fixture.setUp()
    circle = fixture.form()
    return fixture, circle


def recovery_place(fixture, circle, member, office):
    at = support.database_now()
    return models.CircleParticipation.objects.create(circle=circle, membership=member, recovery_office=office,
        seat_id=str(member.membership_uuid), seat_rule_uuid=fixture.lo.founding_authority_basis.basis_uuid,
        actor_state="LIVING_ORGANISM", actor_capacity="STANDING", entry_mode="TRANSPORT", enterer_identity=member.identity,
        composing_rule_state="NA_TRANSPORT", composer_state="NA_TRANSPORT", occurred_at=at, effective_at=at, received_at=at)


def circle_enterer(event):
    # The physical attribution column is deliberately absent from the legacy ORM event.
    with connection.cursor() as cursor:
        cursor.execute("SELECT enterer_identity_id FROM public.core_circlestateevent WHERE id=%s", [event.pk])
        return cursor.fetchone()[0]


def record_due_pair(fixture, loss):
    with transaction.atomic(), connection.cursor() as cur:
        for occurrence in ("OPENED", "DORMANT"):
            cur.execute("SELECT public.circle_record_vacancy(%s,%s,%s,%s)",
                        [fixture.lo.pk, loss.event_uuid, occurrence, fixture.actor.pk])
            if cur.fetchone() != (True,):
                raise AssertionError(f"Missing lawful {occurrence} on the committed loss.")


class CircleVacancyEpisodeTests(TransactionTestCase):
    def test_office_and_recovery_place_check_the_execution_cut(self):
        """W3. Previously valid RC1 and CB1 envelopes do not authorise a backdated ASSIGN."""
        from src.intevia.services import organism_role_service as roles

        fixture, circle = form_with_members()
        founder = fixture.fx.founder_member
        joined = fixture.seat(circle, fixture.fx._bystander())
        loss = leave(fixture, circle, models.CircleParticipation.objects.get(circle=circle, membership=founder))
        due = loss.effective_at + timedelta(hours=720)

        def proposal(actor, key, request):
            return fixture.fx.roles.nominate_circle_coordinator(
                credential=actor.credential, organism_uuid=fixture.lo.organism_id, circle_uuid=circle.circle_uuid,
                participation_uuid=joined.participation_uuid, assignment_uuid=key, request_uuid=request)

        def appointment(actor, key, request):
            return fixture.fx.roles.appoint_circle_coordinator(
                credential=actor.credential, organism_uuid=fixture.lo.organism_id, circle_uuid=circle.circle_uuid,
                assignment_uuid=key, request_uuid=request)

        # Save the actual authorised arguments of each successful write. Undo just the
        # completion so a genuine PROPOSE and its consent are committed before the move.
        attempts = []
        for actor in (joined.membership.identity, fixture.actor):
            key, request = uuid4(), uuid4()
            proposal(actor, key, uuid4())
            office = models.ContextualRoleAssignment.objects.get(assignment_uuid=key)
            if actor == fixture.actor:
                fixture.fx.consent.record_office_consent(fixture.fx._consent_command(joined.membership, office))
            with transaction.atomic(), mock.patch.object(commands, "write_event", wraps=commands.write_event) as append:
                appointment(actor, key, request)
                arguments = append.call_args.args
                self.assertEqual(arguments[3:5], ("ASSIGN", "ACTIVE"))
                self.assertTrue(arguments[6].endswith("|CB1" if actor == fixture.actor else "|RC1"))
                attempts.append(arguments)
                transaction.set_rollback(True)

        # A third, wholly rolled-back proposal supplies the lawful RC1 nomination
        # decision for the identical target, actor, place and request at the late cut.
        key, request = uuid4(), uuid4()
        with transaction.atomic(), mock.patch.object(commands, "write_event", wraps=commands.write_event) as append:
            proposal(joined.membership.identity, key, request)
            proposal_source = append.call_args.args[5]
            transaction.set_rollback(True)
        clock.advance_to(due + timedelta(microseconds=1))
        for args in attempts:
            with self.subTest(actor=args[2].pk), self.assertRaisesMessage(DatabaseError, "CIRCLE_RESEAT_CUT"):
                with transaction.atomic():
                    commands.write_event(*args[:8], due - timedelta(seconds=1))
        with self.assertRaisesMessage(DatabaseError, "CIRCLE_COORDINATOR_MANDATE"), transaction.atomic():
            commands.write_event(*attempts[0][:8], support.database_now())
        with self.assertRaisesMessage(DatabaseError, "CIRCLE_COORDINATOR_MANDATE"), transaction.atomic():
            with mock.patch.object(roles, "_circle_mandate", return_value=proposal_source):
                proposal(joined.membership.identity, key, request)
        # The former Circle office belongs to this same member but is not their LO office.
        with self.assertRaisesMessage(DatabaseError, "CIRCLE_RESEAT_PLACE_SOURCE"), transaction.atomic():
            recovery_place(fixture, circle, founder, loss.assignment)
            transaction.set_rollback(True)  # This assertion targets the immediate guard.
        self.assertFalse(models.CircleStateEvent.objects.filter(circle=circle, action="RESEAT").exists())
        for args in attempts:
            self.assertEqual(list(models.ContextualRoleAssignmentStateEvent.objects.filter(
                assignment_id=args[1].pk).values_list("action", flat=True)), ["PROPOSE"])
        # The exact second appointment at a genuine current cut now succeeds.
        appointment(fixture.actor, attempts[1][1].assignment_uuid, attempts[1][7])
        self.assertEqual(models.CircleStateEvent.objects.get(circle=circle, action="RESEAT").event_uuid,
                         attempts[1][7])

    def test_final_place_closes_a_recorded_vacancy_dormancy(self):
        """W5. A same-state bypass cannot jump dormancy; a sourced final exit can close it."""
        from hashlib import sha256
        from src.intevia.services import circle_work

        fixture, circle = form_with_members()
        founder = fixture.fx.founder_member
        keeper = fixture.fx._bystander()
        final = fixture.seat(circle, keeper)
        neutral = fixture.form()
        fixture.seat(neutral, keeper)
        fixture.seat(neutral, fixture.fx._bystander())
        composed = fixture.form()
        departing = fixture.fx._member()
        last = fixture.seat(composed, departing)
        losses = [leave(fixture, target, models.CircleParticipation.objects.get(circle=target, membership=founder))
                  for target in (neutral, circle, composed)]

        def neutral_write():
            key = uuid4()
            ref = circle_work.opened_reference("Record catalogue", "cataloguer")
            act = f"OPEN_WORK:{key}:{sha256(ref.encode()).hexdigest()}"
            fixture.grant(neutral.circle_uuid, act)
            at = support.database_now()
            _, decision = commands.evidence("default", fixture.lo, neutral.circle_uuid, act,
                                            fixture.actor, fixture.actor, at)
            neutral.refresh_from_db()
            with transaction.atomic():
                commands.write_event("default", neutral, fixture.actor, "OPEN_WORK", neutral.state,
                                     decision, ref, key, at)
            return key

        early = neutral_write()
        clock.advance_to(max(loss.effective_at for loss in losses) + timedelta(hours=720, microseconds=1))
        with self.assertRaisesMessage(DatabaseError, "CIRCLE_DORMANCY_OWED"):
            neutral_write()
        self.assertEqual(list(models.CircleStateEvent.objects.filter(circle=neutral, action="OPEN_WORK")
                              .values_list("event_uuid", flat=True)), [early])
        record_due_pair(fixture, losses[1])
        key = uuid4()
        args = dict(credential=keeper.identity.credential, organism_uuid=fixture.lo.organism_id,
                    circle_uuid=circle.circle_uuid, participation_uuid=final.participation_uuid,
                    request_uuid=key, confirmed=True)
        try:
            original_end = commands.leave(**args)
        except DatabaseError as exc:
            if "CIRCLE_DISSOLUTION_HISTORY" not in str(exc) and "CIRCLE_DISSOLUTION_SOURCE" not in str(exc):
                raise
            self.fail("The last member must be able to leave from recorded dormancy.")
        self.assertEqual(original_end.pk, commands.leave(**args).pk)
        events = models.CircleStateEvent.objects.filter(circle=circle)
        dormant = events.get(action="BECOME_DORMANT")
        closures = list(events.filter(action="DISSOLVE_CIRCLE"))
        self.assertEqual(len(closures), 1, "The last member must be able to leave from recorded dormancy.")
        closed, = closures
        end = models.CircleParticipationEnd.objects.get(participation=final)
        self.assertEqual((closed.prior_state, closed.resulting_state, circle_enterer(closed)),
                         ("DORMANT", "CLOSED", keeper.identity_id))
        self.assertEqual(closed.evidence_reference, f"last-place:{final.participation_uuid}|cause:{key}")
        self.assertEqual((closed.effective_at, closed.occurred_at, closed.received_at),
                         (end.effective_at, end.occurred_at, end.received_at))
        self.assertLess(dormant.sequence, closed.sequence)
        with connection.cursor() as cur:
            cur.execute("SELECT public.circle_effective_state(%s,%s,%s)", [circle.pk, closed.effective_at,
                                                                           support.database_now()])
            self.assertEqual(cur.fetchone()[0], "CLOSED")
        keeper.refresh_from_db()
        self.assertEqual(keeper.state, "ACTIVE")

        from src.intevia.services.organism_membership_service import OrganismMembershipService
        from tests.test_c1b_departure import DepartureTests

        record_due_pair(fixture, losses[2])
        service = OrganismMembershipService(authority=fixture.fx.authority, clock=support.database_now)
        command = DepartureTests().command(departing)
        service.leave_organism(command)
        final_end = models.CircleParticipationEnd.objects.get(participation=last)
        closure = models.CircleStateEvent.objects.get(circle=composed, action="DISSOLVE_CIRCLE")
        self.assertEqual((closure.prior_state, closure.resulting_state, closure.effective_at, closure.evidence_reference),
                         ("DORMANT", "CLOSED", final_end.effective_at,
                          f"last-place:{last.participation_uuid}|cause:{final_end.composed_from_event_uuid}"))
        departing.refresh_from_db()
        self.assertEqual(departing.state, "ENDED")

    def test_parent_assignment_and_reseat_share_one_commit(self):
        """W6. Joining to take the office and seating a consenting member use the same caused edge."""
        fixture, take = form_with_members()
        nominate, earlier = fixture.form(), fixture.form()
        founder = fixture.fx.founder_member
        member = fixture.fx._bystander()
        joined = {}
        losses = {}
        for target in (take, nominate, earlier):
            joined[target.pk] = fixture.seat(target, member)
            losses[target.pk] = leave(fixture, target, models.CircleParticipation.objects.get(
                circle=target, membership=founder))
        def propose(target, actor, key):
            fixture.fx.roles.nominate_circle_coordinator(credential=actor.credential,
                organism_uuid=fixture.lo.organism_id, circle_uuid=target.circle_uuid,
                participation_uuid=joined[target.pk].participation_uuid, assignment_uuid=key, request_uuid=uuid4())
            return models.ContextualRoleAssignment.objects.get(assignment_uuid=key)

        def complete(target, actor, key, request):
            return fixture.fx.roles.appoint_circle_coordinator(credential=actor.credential,
                organism_uuid=fixture.lo.organism_id, circle_uuid=target.circle_uuid,
                assignment_uuid=key, request_uuid=request)

        def paired(target, key):
            items = list(models.CircleStateEvent.objects.filter(circle=target, action="RESEAT"))
            self.assertEqual(len(items), 1, "The appointment must have exactly one reseat citing it.")
            event = items[0]
            assigned = models.ContextualRoleAssignmentStateEvent.objects.get(assignment__assignment_uuid=key,
                                                                             action="ASSIGN")
            self.assertEqual((event.event_uuid, event.effective_at, circle_enterer(event)),
                             (assigned.event_uuid, assigned.effective_at, fixture.actor.pk))
            self.assertTrue(assigned.evidence_reference.endswith("|CB1"))
            self.assertEqual(event.evidence_reference, f"vacancy:{losses[target.pk].event_uuid}|assign:{assigned.event_uuid}")
            target.refresh_from_db()
            self.assertEqual(target.state, "ACTIVE")
            return event.pk

        early = propose(earlier, member.identity, uuid4())
        clock.advance_to(max(e.effective_at for e in losses.values()) + timedelta(hours=720, microseconds=1))
        # R5 A. The member's earlier proposal is their consent. The LO supplies the appointment.
        with self.assertRaises(places.CircleAccessRefused):
            complete(earlier, member.identity, early.assignment_uuid, uuid4())
        key = uuid4()
        complete(earlier, fixture.actor, early.assignment_uuid, key)
        paired(earlier, early.assignment_uuid)
        # Nomination of another member after recorded dormancy needs that member's actual consent.
        record_due_pair(fixture, losses[nominate.pk])
        prior_assignment = models.ContextualRoleAssignmentStateEvent.objects.get(
            assignment=losses[nominate.pk].assignment, action="ASSIGN")
        with self.assertRaisesMessage(DatabaseError, "CIRCLE_RESEAT_SOURCE"), transaction.atomic():
            with connection.cursor() as cur:
                at = support.database_now()
                head = models.CircleStateEvent.objects.filter(circle=nominate).latest("sequence")
                ref = f"vacancy:{losses[nominate.pk].event_uuid}|assign:{prior_assignment.event_uuid}"
                attempt = f"circle-vacancy:{losses[nominate.pk].event_uuid}:RESEAT"
                cur.execute("""SELECT public.s015_append_event('core_circle',%s,'RESEAT','ACTIVE',%s,%s,%s,
                         %s,%s,%s,%s,%s,public.c2_target(%s),'{}',NULL,%s,%s,%s,NULL,'CIRCLES:VACANCY',%s)""",
                            [nominate.pk, at, at, at, fixture.actor.pk, prior_assignment.authority_basis_id,
                             prior_assignment.authority_decision_reference, ref, attempt, ref, prior_assignment.event_uuid,
                             head.pk, head.sequence, attempt])
        nominee = propose(nominate, fixture.actor, uuid4())
        with self.assertRaisesMessage(places.CircleAccessRefused, "current consent"):
            complete(nominate, fixture.actor, nominee.assignment_uuid, uuid4())
        fixture.fx.consent.record_office_consent(fixture.fx._consent_command(member, nominee))
        request = uuid4()
        complete(nominate, fixture.actor, nominee.assignment_uuid, request)
        committed = paired(nominate, nominee.assignment_uuid)
        self.assertEqual(complete(nominate, fixture.actor, nominee.assignment_uuid, request), request)
        self.assertEqual(paired(nominate, nominee.assignment_uuid), committed)
        # No place survives a rolled-back join-and-take, including when settlement was silent.
        office_key = uuid4()
        parent = models.ContextualRoleAssignment.objects.get(membership=founder, circle__isnull=True,
                                                               role_definition__code="COORDINATOR")
        def take_office():
            with transaction.atomic():
                place = recovery_place(fixture, take, founder, parent)
                return commands._take_circle_office("default", fixture.lo, fixture.actor.credential,
                                                    take, place, office_key)
        with transaction.atomic():
            take_office()
            paired(take, office_key)
            transaction.set_rollback(True)
        self.assertFalse(models.CircleStateEvent.objects.filter(circle=take, action__in=(
            "OPEN_VACANCY", "BECOME_DORMANT", "RESEAT")).exists())
        self.assertFalse(models.CircleParticipation.objects.filter(circle=take, recovery_office=parent).exists())
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(assignment_uuid=office_key).exists())
        self.assertEqual(take_office(), office_key)
        committed = paired(take, office_key)
        place = models.CircleParticipation.objects.get(circle=take, recovery_office=parent)
        self.assertEqual(commands._take_circle_office("default", fixture.lo, fixture.actor.credential,
                                                      take, place, office_key), office_key)
        self.assertEqual(paired(take, office_key), committed)

    def test_two_parent_issuers_cannot_take_the_same_vacancy(self):
        """W6 race. Two qualified LO holders attempt the actual coupled place and office writes."""
        from concurrent.futures import ThreadPoolExecutor
        from src.intevia.services.organism_coverage import office_scope
        from tests.root_genesis_support import external_finding

        fixture, circle = form_with_members()
        founder = fixture.fx.founder_member
        incumbent = models.ContextualRoleAssignment.objects.get(membership=founder, circle__isnull=True,
                                                                 role_definition__code="COORDINATOR")
        colleague, other = fixture.fx._appoint(incumbent.role_definition)
        fixture.seat(circle, fixture.fx._bystander())
        loss = leave(fixture, circle, models.CircleParticipation.objects.get(circle=circle, membership=founder))
        clock.advance_to(loss.effective_at + timedelta(hours=720, microseconds=1))
        issuers = [(founder, incumbent, uuid4()), (colleague, other, uuid4())]
        for person, office, _ in issuers:
            assign = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=office, action="ASSIGN")
            external_finding(office_scope(fixture.lo, office, assign.authority_basis))
            with connection.cursor() as cur:
                at = support.database_now()
                cur.execute("SELECT public.circle_lo_office(%s,%s,%s,%s)", [fixture.lo.pk, person.identity_id, at, at])
                self.assertEqual(cur.fetchone(), (office.assignment_uuid,))
        barrier = threading.Barrier(2, timeout=10)
        def take(person, office, key):
            try:
                credential = person.identity.credential
                barrier.wait()
                with commands.command_context("default", credential, fixture.lo.organism_id, require_member=True):
                    place = recovery_place(fixture, circle, person, office)
                    commands._take_circle_office("default", fixture.lo, credential, circle, place, key)
                return "SEATED"
            except DatabaseError as exc:
                if "CIRCLE_RESEAT_PLACE_SOURCE" not in str(exc):
                    raise
                return "REFUSED"
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(take, *issuer) for issuer in issuers]
            states = [result.result(timeout=30) for result in results]
        self.assertCountEqual(states, ["SEATED", "REFUSED"])
        person, office, key = issuers[states.index("SEATED")]
        place = models.CircleParticipation.objects.get(circle=circle, recovery_office__isnull=False)
        self.assertEqual((place.membership_id, place.recovery_office_id), (person.pk, office.pk))
        self.assertEqual(commands._take_circle_office("default", fixture.lo, person.identity.credential,
                                                      circle, place, key), key)
        self.assertEqual(models.ContextualRoleAssignment.objects.filter(assignment_uuid__in=[i[2] for i in issuers]).count(), 1)
        self.assertEqual(models.CircleStateEvent.objects.filter(circle=circle, action="RESEAT").count(), 1)

    def test_last_loss_owns_the_half_open_deadline(self):
        """W1. An earlier partial loss and a recovered loss cannot determine this deadline."""
        fixture, circle = form_with_members()
        founder = fixture.fx.founder_member
        first = models.CircleParticipation.objects.get(circle=circle, membership=founder)
        second = fixture.seat(circle, fixture.fx._bystander())
        third = fixture.seat(circle, fixture.fx._bystander())
        fourth = fixture.seat(circle, fixture.fx._bystander())
        fixture.appoint(circle, founder, second)
        leave(fixture, circle, first)
        cut = support.database_now()
        self.assertIsNone(episode(circle, cut, cut), "One Coordinator still holds the office.")
        loss_a = leave(fixture, circle, second)
        tick = timedelta(microseconds=1)
        period = timedelta(hours=720)
        known = support.database_now()
        self.assertIsNone(episode(circle, loss_a.effective_at, cut))
        self.assertEqual(episode(circle, known, known),
                         (loss_a.event_uuid, loss_a.effective_at, loss_a.effective_at + period, "OPEN"))
        commands.recover_circle(credential=third.membership.identity.credential,
                                organism_uuid=fixture.lo.organism_id, circle_uuid=circle.circle_uuid,
                                request_uuid=uuid4())
        known = support.database_now()
        self.assertIsNone(episode(circle, known, known))
        loss = leave(fixture, circle, third)
        due = loss.effective_at + period
        clock.advance_to(due + tick)
        known = support.database_now()
        self.assertGreater(known, due)
        self.assertEqual(episode(circle, loss_a.effective_at + period, known),
                         (loss.event_uuid, loss.effective_at, due, "OPEN"))
        for at, status, state, vacant in ((due - tick, "OPEN", "ACTIVE", True),
                                         (due, "EXPIRED", "DORMANT", False)):
            with self.subTest(at=at), connection.cursor() as cursor:
                self.assertEqual(episode(circle, at, known), (loss.event_uuid, loss.effective_at, due, status))
                cursor.execute("SELECT public.circle_effective_state(%s,%s,%s),public.circle_vacant(%s,NULL,%s,%s)",
                               [circle.pk, at, known, circle.pk, at, known])
                self.assertEqual(cursor.fetchone(), (state, vacant), "The member route closes at equality.")
        count = models.ContextualRoleAssignment.objects.filter(circle=circle).count()
        with self.assertRaises(places.CircleAccessRefused):
            commands.recover_circle(credential=fourth.membership.identity.credential,
                                    organism_uuid=fixture.lo.organism_id, circle_uuid=circle.circle_uuid,
                                    request_uuid=uuid4())
        self.assertEqual(models.ContextualRoleAssignment.objects.filter(circle=circle).count(), count)
        # Only the episode orders the cut against the deadline; its consumers take the status.
        with connection.cursor() as cursor:
            texts = [migration.definition(cursor, sig) for sig in
                     (records.EXIT_EPISODE, migration.BEFORE_ASSIGN, migration.EPISODE, migration.EFFECTIVE, migration.VACANT)]
        self.assertEqual(sum(t.count("at_time<p.fixed_end") for t in texts), 1)
        for consumer in texts[1:]:
            self.assertNotIn("interval '720 hours'", consumer)
            self.assertNotRegex(consumer, r"(?i)(?:<=?|>=?)\s*(?:\w+\.)?(?:deadline|fixed_end)\b")

    def test_0105_requires_exact_predecessor_and_successor(self):
        """W12. Both directions refuse drift; restoring the expected bytes permits a round trip."""
        care = import_module("core.migrations.0111_care_office_record")
        account = import_module("core.migrations.0113_care_system_account")
        with connection.schema_editor(atomic=True) as editor, connection.cursor() as cursor:
            def altered(sql):
                return sql.replace("AS $function$\n", "AS $function$\n-- W12 drift\n", 1)

            def drift_trigger(source, token, name):
                cursor.execute("DROP TRIGGER " + name + " ON public.core_circlestateevent")
                cursor.execute(source.replace(token, "'W12_DRIFT'"))

            def check_drift(name, source, token, operation, installed):
                drift_trigger(source, token, name)
                before = migration.definition(cursor, records.WRITER)
                with self.assertRaisesMessage(RuntimeError, "0106 installed trigger differs: " + name):
                    operation(None, editor)
                self.assertEqual(migration.definition(cursor, records.WRITER), before,
                                 "The mismatched installed trigger must refuse without changing another object.")
                self.assertEqual(before == records.WRITE_SQL, installed)
                cursor.execute("DROP TRIGGER " + name + " ON public.core_circlestateevent")
                cursor.execute(source)

            account.uninstall(None, editor)
            care.uninstall(None, editor)
            chair.uninstall(None, editor)
            signature, before, after = reseat.REPLACEMENTS[0]
            cursor.execute(altered(after))
            with self.assertRaisesMessage(RuntimeError, "0108 installed definition differs: " + signature):
                reseat.uninstall(None, editor)
            self.assertEqual(migration.definition(cursor, reseat.RESEATABLE), reseat.RESEATABLE_SQL)
            cursor.execute(after)
            reseat.uninstall(None, editor)
            cursor.execute(altered(before))
            with self.assertRaisesMessage(RuntimeError, "0108 installed definition differs: " + signature):
                reseat.install(None, editor)
            self.assertIsNone(migration.definition(cursor, reseat.RESEATABLE))
            cursor.execute(before)
            signature, before, after = committee_cut.REPLACEMENTS[0]
            cursor.execute(altered(after))
            with self.assertRaisesMessage(RuntimeError, "0107 installed definition differs: " + signature):
                committee_cut.uninstall(None, editor)
            self.assertEqual(migration.definition(cursor, committee_cut.APART), committee_cut.APART_SQL)
            cursor.execute(after)
            committee_cut.uninstall(None, editor)
            cursor.execute(altered(before))
            with self.assertRaisesMessage(RuntimeError, "0107 installed definition differs: " + signature):
                committee_cut.install(None, editor)
            self.assertIsNone(migration.definition(cursor, committee_cut.APART))
            cursor.execute(before)
            check_drift("zz_cmt_change", records.REFRESH_SOURCE_NEW, "'RESEAT'", records.uninstall, True)
            check_drift("zz_circle_qualified_activation", records.GUARD_SOURCE_NEW, "'RESEAT'", records.uninstall, True)
            records.uninstall(None, editor)
            check_drift("zz_cmt_change", records.REFRESH_TRIGGER, "'CLOSED'", records.install, False)
            check_drift("zz_circle_qualified_activation", records.GUARD_SOURCE, "'ACTIVATE'", records.install, False)
            cursor.execute(altered(migration.REDUCE_0105))
            with self.assertRaisesMessage(RuntimeError, migration.REDUCE):
                migration.uninstall(None, editor)
            self.assertEqual(migration.definition(cursor, migration.VACANT), migration.VACANT_0105)
            cursor.execute(migration.REDUCE_0105)
            cursor.execute(altered(migration.STATE_DEFINITION))
            with self.assertRaisesMessage(RuntimeError, migration.EFFECTIVE):
                migration.uninstall(None, editor)
            self.assertEqual(migration.definition(cursor, migration.VACANT), migration.VACANT_0105)
            cursor.execute(migration.STATE_DEFINITION)
            migration.uninstall(None, editor)
            self.assertEqual(migration.definition(cursor, migration.VACANT), migration.VACANT_0104)
            for signature, _ in migration.ADDITIONS:
                self.assertIsNone(migration.definition(cursor, signature))
            self.assertEqual(migration.definition(cursor, migration.REDUCE), migration.REDUCE_0104)
            cursor.execute(altered(migration.REDUCE_0104))
            with self.assertRaisesMessage(RuntimeError, migration.REDUCE):
                migration.install(None, editor)
            cursor.execute(migration.REDUCE_0104)
            cursor.execute(altered(migration.VACANT_0104))
            with self.assertRaisesMessage(RuntimeError, migration.VACANT):
                migration.install(None, editor)
            for signature, _ in migration.ADDITIONS:
                self.assertIsNone(migration.definition(cursor, signature))
            cursor.execute(migration.VACANT_0104)
            migration.install(None, editor)
            self.assertEqual(migration.definition(cursor, migration.VACANT), migration.VACANT_0105)
            for signature, sql in migration.ADDITIONS:
                self.assertEqual(migration.definition(cursor, signature), sql)
            self.assertEqual(migration.definition(cursor, migration.REDUCE), migration.REDUCE_0105)
            records.install(None, editor)
            records.expect(cursor, True)
            committee_cut.install(None, editor)
            committee_cut.expect(cursor, True)
            reseat.install(None, editor)
            reseat.expect(cursor, True)
            chair.install(None, editor)
            care.install(None, editor)
            transaction.set_rollback(True)  # Leave this test's schema exactly as it entered.

        # An ordinary LO founding and phase advancement commit before the reducer comparison.
        from tests import test_sr_office_actions as offices

        fixture = offices.SROfficeActionTests()
        fixture.setUp()
        cut = support.database_now()
        with connection.schema_editor(atomic=True) as editor, connection.cursor() as cursor:
            cursor.execute("SELECT public.s015_eligible_event_ids('core_livingorganism',%s,%s,%s)",
                           [fixture.organism.pk, cut, cut])
            ids = cursor.fetchone()[0]
            self.assertGreaterEqual(len(ids), 2)

            def lo_reading():
                result = []
                for prefix in ([], ids[:1], ids):
                    cursor.execute("SELECT public.s015_reduce_projection('core_livingorganism',%s::bigint[])", [prefix])
                    result.append(cursor.fetchone()[0])
                cursor.execute("SELECT public.s015_fold_effective_state('core_livingorganism',id,%s,%s),state,"
                               "public.s015_requalify_governed_cache('core_livingorganism',id),head_event_id "
                               "FROM public.core_livingorganism WHERE id=%s", [cut, cut, fixture.organism.pk])
                return result, cursor.fetchone()

            account.uninstall(None, editor)
            care.uninstall(None, editor)
            chair.uninstall(None, editor)
            reseat.uninstall(None, editor)
            committee_cut.uninstall(None, editor)
            successor = lo_reading()
            self.assertIsNone(successor[0][0])
            self.assertEqual(successor[1], ("ACTIVE", "ACTIVE", True, ids[-1]))
            records.uninstall(None, editor)
            migration.uninstall(None, editor)
            self.assertEqual(lo_reading(), successor, "Living-organism prefixes and cache must match 0104.")
            migration.install(None, editor)
            records.install(None, editor)
            committee_cut.install(None, editor)
            reseat.install(None, editor)
            chair.install(None, editor)
            care.install(None, editor)
            self.assertEqual(lo_reading(), successor)
            transaction.set_rollback(True)

        # Reach the real BEFORE INSERT guard with two edges the old predicate skipped.
        circle_fixture, target = form_with_members()
        head = models.CircleStateEvent.objects.filter(circle=target).latest("sequence")
        for action, prior, refusal in (("RESEAT", "DORMANT", "CIRCLE_RESEAT_SOURCE"),
                                       ("BECOME_DORMANT", "ELIGIBLE", "CIRCLE_VACANCY_SOURCE")):
            with self.subTest(action=action), self.assertRaisesMessage(DatabaseError, refusal), transaction.atomic():
                with connection.cursor() as cursor:
                    at = support.database_now()
                    support.append_event(cursor, "core_circlestateevent", target.pk, head.sequence + 1,
                                         action, prior, "DORMANT", circle_fixture.actor.pk, circle_fixture.subject.pk,
                                         action, pred_id=head.pk, eff=at, occ=at, rec=at)
        target.refresh_from_db()
        self.assertEqual(target.head_event_id, head.pk)

    test_0105_requires_exact_predecessor_and_successor.ISOLATION_FULL_CAPTURE = True

    def test_0109_refuses_drift_before_changing_any_pinned_function(self):
        """0109 refuses a single mismatched definition before changing any of its four functions."""
        predecessors = {
            chair.RESERVED: None,
            chair.EMPTY: committee_cut.EMPTY_NEW,
            reseat.RESEATABLE: reseat.RESEATABLE_SQL,
            chair.WORK_PLAN: committee_cut.WORK_PLAN_NEW,
        }
        successors = {
            chair.RESERVED: chair.RESERVED_SQL,
            chair.EMPTY: chair.EMPTY_SQL,
            reseat.RESEATABLE: chair.RESEATABLE_SQL,
            chair.WORK_PLAN: chair.WORK_PLAN_SQL,
        }

        def installed(cursor):
            return {sig: migration.definition(cursor, sig) for sig in predecessors}

        with connection.schema_editor(atomic=True) as editor, connection.cursor() as cursor:
            def check_each(operation, clean):
                for sig, body in clean.items():
                    with self.subTest(operation=operation.__name__, signature=sig):
                        with transaction.atomic():
                            if body is None:
                                # Even the correct helper body violates the pin if it exists early.
                                drift = successors[sig]
                            else:
                                # W12's source-comment alteration retains the function signature.
                                drift = body.replace("AS $function$\n", "AS $function$\n-- W12 drift\n", 1)
                            self.assertNotEqual(drift, body, f"The fixture must change the definition of {sig}.")
                            cursor.execute(drift)
                            tampered = clean | {sig: drift}
                            self.assertEqual(installed(cursor), tampered,
                                             f"Only {sig} may differ before the attempted migration.")
                            with self.assertRaises(RuntimeError, msg=f"{operation.__name__} must refuse drift in {sig}.") as refusal:
                                operation(None, editor)
                            self.assertEqual(str(refusal.exception), "0109 installed definition differs: " + sig,
                                             f"The refusal must identify the exact mismatched function {sig}.")
                            self.assertEqual(installed(cursor), tampered,
                                             "The refusal must leave the altered function and its three neighbours untouched.")
                            transaction.set_rollback(True)
                        self.assertEqual(installed(cursor), clean,
                                         f"Rolling back the change to {sig} must restore all four pins.")

            self.assertEqual(installed(cursor), successors, "The sandbox must start at the complete 0109 head.")
            chair.uninstall(None, editor)
            self.assertEqual(installed(cursor), predecessors, "The forward trials must start at the exact 0108 definitions.")
            check_each(chair.install, predecessors)
            chair.install(None, editor)
            self.assertEqual(installed(cursor), successors, "The reverse trials must start at the exact 0109 definitions.")
            check_each(chair.uninstall, successors)

            chair.uninstall(None, editor)
            self.assertEqual(installed(cursor), predecessors,
                             "A clean reversal must remove the new helper and restore the three original definitions.")
            chair.install(None, editor)
            self.assertEqual(installed(cursor), successors, "A clean reinstall must restore all four 0109 definitions.")
            transaction.set_rollback(True)

    test_0109_refuses_drift_before_changing_any_pinned_function.ISOLATION_FULL_CAPTURE = True

    def test_vacancy_records_keep_the_causal_times(self):
        """W4. Each due occurrence retains its own cause, domain dates and unique effect."""
        fixture, circle = form_with_members()
        branch = fixture.form()
        first = models.CircleParticipation.objects.get(circle=branch, membership=fixture.fx.founder_member)
        m1 = fixture.seat(branch, fixture.fx._bystander())
        m2 = fixture.seat(branch, fixture.fx._bystander())
        founder_place = models.CircleParticipation.objects.get(circle=circle, membership=fixture.fx.founder_member)
        fixture.seat(circle, fixture.fx._bystander())
        loss = leave(fixture, circle, founder_place)
        due = loss.effective_at + timedelta(hours=720)
        ref = "vacancy:" + str(loss.event_uuid)

        def write(occurrence):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT public.circle_record_vacancy(%s,%s,%s,%s)",
                               [fixture.lo.pk, loss.event_uuid, occurrence, fixture.actor.pk])
                return cursor.fetchone()[0]

        def facts():
            with connection.cursor() as cursor:
                cursor.execute("""SELECT action,prior_state,resulting_state,effective_at,occurred_at,effective_until,
                    received_at,recorded_at,temporal_basis_kind,enterer_identity_id,composing_rule_reference,
                    request_reference,event_uuid,actor_state,entry_mode,composer_identity_id,
                    authority_basis_id,authority_decision_reference='s015d1:'||public.c2_target(evidence_reference),
                    idempotency_key=request_reference
                    FROM public.core_circlestateevent WHERE circle_id=%s AND evidence_reference=%s
                    ORDER BY sequence""", [circle.pk, ref])
                return cursor.fetchall()

        self.assertFalse(write("DORMANT"))
        self.assertTrue(write("OPENED"))
        self.assertFalse(write("OPENED"))
        self.assertFalse(write("DORMANT"))
        opening, = facts()
        self.assertEqual(opening[:6], ("OPEN_VACANCY", "ACTIVE", "ACTIVE", loss.effective_at, loss.effective_at, due))
        clock.advance_to(due + timedelta(microseconds=1))
        message = "The dormancy must be effective at the deadline and recorded later."
        try:
            changed = write("DORMANT")
        except DatabaseError as error:
            if "CIRCLE_VACANCY_SOURCE" not in str(error):
                raise
            self.fail(message)
        self.assertTrue(changed, message)
        original, dormant = facts()
        self.assertEqual(original, opening)
        self.assertEqual(dormant[:6], ("BECOME_DORMANT", "ACTIVE", "DORMANT", due, due, None), message)
        self.assertGreater(dormant[7], due, message)
        for fact in (opening, dormant):
            self.assertLess(fact[3], fact[6])
            self.assertLessEqual(fact[6], fact[7])
            self.assertEqual(fact[8:12], ("RETROSPECTIVE", fixture.actor.pk, "CIRCLES:VACANCY",
                                          f"circle-vacancy:{loss.event_uuid}:{fact[0]}"))
            self.assertEqual(fact[13:], ("NONE", "COMPOSED", fixture.actor.pk, fixture.subject.pk, True, True))
        self.assertFalse(write("DORMANT"))
        self.assertEqual(facts(), [opening, dormant])
        circle.refresh_from_db()
        self.assertEqual(circle.state, "DORMANT")

        # Prior committed effects cannot acquire an OPENED receipt in this transaction.
        with self.assertRaisesMessage(DatabaseError, "TE_RECEIPT_ORIGIN"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("""INSERT INTO public.core_tereceipt
                    (organism_id,consumer,purpose,source_ref,occurrence,outcome,effect_ref,rule_ref,
                     enterer_id,due_at,effect_at)
                    VALUES (%s,'CIRCLE','VACANCY',%s,'OPENED','OPENED',%s,'CIRCLES:VACANCY',%s,%s,%s)""",
                               [fixture.lo.pk, str(loss.event_uuid), str(opening[12]), fixture.actor.pk,
                                opening[3], opening[7]])
        turn = circle_turn(fixture)
        self.assertEqual((turn.result, circle_receipts(fixture), facts()), ("IDLE", [], [opening, dormant]),
                         "Completed occurrences must not acquire a new receipt.")

        loss_a = leave(fixture, branch, first)
        # Use the real runner's fault seam after the committed leave, before the receipt.
        with mock.patch.object(te, "_between_effect_and_receipt", side_effect=RuntimeError("W4 rollback")) as fault:
            failed = circle_turn(fixture)
        fault.assert_called_once()
        self.assertEqual((failed.result, failed.source_ref), ("FAILED", str(loss_a.event_uuid)))
        self.assertFalse(models.CircleStateEvent.objects.filter(
            circle=branch, evidence_reference="vacancy:" + str(loss_a.event_uuid)).exists())
        self.assertEqual(circle_receipts(fixture), [])
        first_turn = circle_turn(fixture)
        self.assertEqual([(r[0], r[1], r[3]) for r in circle_receipts(fixture)],
                         [(str(loss_a.event_uuid), "OPENED", loss_a.effective_at)],
                         "The opening and its same-transaction receipt must commit together.")
        self.assertEqual((first_turn.result, first_turn.source_ref), ("OPENED", str(loss_a.event_uuid)))

        def recover(place):
            return commands.recover_circle(credential=place.membership.identity.credential,
                                           organism_uuid=fixture.lo.organism_id, circle_uuid=branch.circle_uuid,
                                           request_uuid=uuid4())

        period = timedelta(hours=720)
        clock.advance_to(loss_a.effective_at + timedelta(days=10))
        recovered = recover(m1)
        self.assertTrue(models.ContextualRoleAssignmentStateEvent.objects.filter(
            assignment__assignment_uuid=recovered, action="ASSIGN").exists())
        clock.advance_to(loss_a.effective_at + timedelta(days=11))
        loss_b = leave(fixture, branch, m1)
        clock.advance_to(loss_a.effective_at + period + timedelta(microseconds=1))
        cut = support.database_now()
        due_b = loss_b.effective_at + period
        self.assertEqual(episode(branch, cut, cut), (loss_b.event_uuid, loss_b.effective_at, due_b, "OPEN"),
                         "The second loss must still have its own recovery period.")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT public.circle_record_vacancy(%s,%s,'DORMANT',%s)",
                           [fixture.lo.pk, loss_a.event_uuid, fixture.actor.pk])
            self.assertFalse(cursor.fetchone()[0], "A recovered loss cannot settle the later loss.")
        second = circle_turn(fixture)
        self.assertEqual((second.result, second.source_ref), ("OPENED", str(loss_b.event_uuid)))
        self.assertFalse(models.CircleStateEvent.objects.filter(circle=branch, action="BECOME_DORMANT").exists())
        with transaction.atomic():
            alternate = recover(m2)
            cut = support.database_now()
            self.assertIsNone(episode(branch, cut, cut), "The continuing participant can take the second vacancy.")
            transaction.set_rollback(True)
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(assignment_uuid=alternate).exists())

        clock.advance_to(due_b + timedelta(microseconds=1))
        last = circle_turn(fixture)
        expected = [(loss_a, "OPENED", "OPEN_VACANCY", loss_a.effective_at),
                    (loss_b, "OPENED", "OPEN_VACANCY", loss_b.effective_at),
                    (loss_b, "DORMANT", "BECOME_DORMANT", due_b)]
        receipts = circle_receipts(fixture)
        self.assertEqual([(r[0], r[1]) for r in receipts], [(str(e.event_uuid), occ) for e, occ, _, _ in expected],
                         "Only loss B may acquire a dormancy receipt.")
        self.assertEqual((last.result, last.source_ref), ("OPENED", str(loss_b.event_uuid)))
        for receipt, (end, occurrence, action, domain_cut) in zip(receipts, expected):
            effect = models.CircleStateEvent.objects.get(circle=branch, action=action,
                                                         evidence_reference="vacancy:" + str(end.event_uuid))
            self.assertEqual(receipt[:5], (str(end.event_uuid), occurrence, str(effect.event_uuid),
                                           domain_cut, effect.recorded_at))
            self.assertEqual((effect.effective_at, effect.occurred_at), (domain_cut, domain_cut))
            self.assertLess(domain_cut, effect.recorded_at)
            self.assertGreaterEqual(receipt[5], effect.recorded_at)
            self.assertEqual(receipt[6:], (fixture.actor.pk, "OPENED", "CIRCLES:VACANCY"))
        self.assertEqual(circle_turn(fixture).result, "IDLE")
        self.assertEqual(circle_receipts(fixture), receipts)

    def test_circle_claim_skips_a_held_target_without_charging(self):
        """W11. A contended opening or refresh leaves the due occurrence and its probe untouched."""
        fixture, circle = form_with_members()
        founder_place = models.CircleParticipation.objects.get(circle=circle, membership=fixture.fx.founder_member)
        fixture.seat(circle, fixture.fx._bystander())
        loss = leave(fixture, circle, founder_place)
        # Let both cuts pass before any wake. Only the opening may be made on the first successful turn.
        clock.advance_to(loss.effective_at + timedelta(hours=720, microseconds=1))

        def untouched():
            circle.refresh_from_db()
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM public.core_tepace WHERE organism_id=%s AND consumer='CIRCLE'",
                               [fixture.lo.pk])
                pace = cursor.fetchall()
                cursor.execute("SELECT count(*) FROM public.core_cmtcustody")
                custody = cursor.fetchone()[0]
            return (circle.head_event_id, circle.state, circle_receipts(fixture), pace, custody)

        locks = [
            ("OPENED", "SELECT id FROM public.core_circle WHERE id=%s FOR UPDATE", circle.pk),
            ("DORMANT", "SELECT id FROM public.core_contextualroleassignment WHERE id=%s FOR UPDATE", loss.assignment_id),
        ]
        for occurrence, lock, target in locks:
            with self.subTest(occurrence=occurrence):
                election_support.idle_until_turn_of(fixture.lo, fixture.actor, "CIRCLE")
                before = untouched()
                held, release, expired = threading.Event(), threading.Event(), threading.Event()
                errors = []

                def hold():
                    try:
                        with transaction.atomic(), connection.cursor() as cursor:
                            cursor.execute(lock, [target])
                            if cursor.fetchone() != (target,):
                                raise AssertionError("W11 failed to lock the named target.")
                            held.set()
                            if not release.wait(30):
                                expired.set()
                    except Exception as error:
                        errors.append(type(error).__name__)
                        held.set()
                    finally:
                        connections.close_all()

                peer = threading.Thread(target=hold)
                peer.start()
                try:
                    self.assertTrue(held.wait(30), "W11 peer never acquired the row.")
                    self.assertEqual(errors, [])
                    skipped = te.run(circle_wake(fixture), alias="default")
                    self.assertFalse(expired.is_set(), "W11 must skip while the peer still holds the row.")
                    self.assertTrue(peer.is_alive())
                    self.assertEqual((skipped.result, skipped.consumer), ("BUSY_INNER", None),
                                     "W11 must skip the contended anchor or custody refresh.")
                    self.assertEqual(untouched(), before, "A skip must leave effects, receipts, pace and custody intact.")
                finally:
                    release.set()
                    peer.join(30)
                self.assertFalse(peer.is_alive())
                self.assertEqual(errors, [])
                retry = te.run(circle_wake(fixture), alias="default")
                receipts = circle_receipts(fixture)
                self.assertEqual(len(receipts), len(before[2]) + 1)
                self.assertEqual((retry.result, receipts[-1][:2]), ("OPENED", (str(loss.event_uuid), occurrence)))
                circle.refresh_from_db()
                self.assertEqual(circle.state, "ACTIVE" if occurrence == "OPENED" else "DORMANT")

    def test_expired_volunteer_sees_the_cause_at_the_entitled_cut(self):
        """W2. The same volunteer gets the effective deadline before and after its storage record."""
        from datetime import timezone
        from unittest.mock import patch

        fixture, circle = form_with_members()
        previous = fixture.seat(circle, fixture.fx._bystander())
        joined = fixture.seat(circle, fixture.fx._bystander())
        outsider = fixture.fx._bystander()
        leave(fixture, circle, models.CircleParticipation.objects.get(circle=circle, membership=fixture.fx.founder_member))
        def args(member):
            return dict(credential=member.identity.credential, organism_uuid=fixture.lo.organism_id,
                        circle_uuid=circle.circle_uuid)
        earlier = args(previous.membership) | {"request_uuid": uuid4()}
        original_office = commands.recover_circle(**earlier)
        loss = leave(fixture, circle, previous)
        deadline = loss.effective_at + timedelta(hours=720)
        allowed = args(joined.membership)
        self.assertEqual(commands.operating_status_detail(**allowed)["deadline"], deadline)
        self.assertEqual(commands.operating_status(**allowed), "WITHOUT_COORDINATOR")
        clock.advance_to(deadline + timedelta(days=1))
        stamp = deadline.astimezone(timezone.utc).strftime("%d %B %Y at %H:%M UTC").lstrip("0")
        tail = (" Nothing changed and your place is kept. Ask an LO Coordinator to reseat the Circle. "
                "The LO's Coordinators handle this.")
        for recorded in (False, True):
            if recorded:
                record_due_pair(fixture, loss)
            history = models.CircleStateEvent.objects.filter(circle=circle)
            unchanged = tuple(history.order_by("sequence").values_list("event_uuid", flat=True))
            why = (f"This Circle became dormant on {stamp} because it had no Coordinator." if recorded
                   else f"This Circle's recovery period ended at {stamp}.")
            with self.subTest(recorded=recorded), self.assertRaises(places.CircleAccessRefused) as refused:
                commands.recover_circle(**allowed, request_uuid=uuid4())
            self.assertEqual(str(refused.exception), "You were not appointed. " + why + tail,
                             "A volunteer must receive the reason for this record at its effective deadline.")
            self.assertEqual(commands.operating_status(**allowed), "DORMANT")
            self.assertEqual(commands.operating_status_detail(**allowed)["deadline"], deadline)
            self.assertIn(stamp, commands.operating_status_detail(**allowed)["message"])
            # Privacy must be decided before any episode detail is read.
            with patch.object(commands, "_vacancy_at", side_effect=AssertionError("disclosed Circle episode")):
                for action, extra in ((commands.operating_status_detail, {}), (commands.recover_circle, {"request_uuid": uuid4()})):
                    with self.assertRaisesMessage(places.CircleAccessRefused, "This request cannot use the selected Circle"):
                        action(**args(outsider), **extra)
            self.assertEqual(commands.recover_circle(**earlier), original_office)
            self.assertEqual(tuple(history.order_by("sequence").values_list("event_uuid", flat=True)), unchanged)
            self.assertFalse(models.CircleParticipationEnd.objects.filter(participation=joined).exists())
        # A real qualification finding can withdraw the sole parent issuer without
        # changing this member's place. Roll it back before the independent positive.
        from src.intevia.services.organism_coverage import office_scope
        from tests.root_genesis_support import external_instrument

        founder_assignment = models.ContextualRoleAssignmentStateEvent.objects.get(
            assignment__membership=fixture.fx.founder_member, assignment__circle__isnull=True,
            assignment__role_definition__code="COORDINATOR", action="ASSIGN")
        self.assertTrue(commands._has_lo_coordinator("default", fixture.lo, support.database_now()))
        with transaction.atomic():
            external_instrument(office_scope(fixture.lo, founder_assignment.assignment,
                                             founder_assignment.authority_basis)).record(result="REFUSED")
            self.assertFalse(commands._has_lo_coordinator("default", fixture.lo, support.database_now()))
            with self.assertRaises(places.CircleAccessRefused) as no_issuer:
                commands.recover_circle(**allowed, request_uuid=uuid4())
            self.assertEqual(str(no_issuer.exception),
                f"You were not appointed. This Circle became dormant on {stamp} because it had no Coordinator, "
                "and the LO currently has no Coordinator. Your place is kept. Follow the LO’s existing Coordinator "
                "recovery process. Once in office, an LO Coordinator can take the Circle office or nominate a member who accepts.")
            self.assertIn("the LO currently has no Coordinator", commands.operating_status_detail(**allowed)["message"])
            transaction.set_rollback(True)
        # A vacant office gives nobody the holder-only claim read. Check the
        # approved formatting independently without bypassing that authority test.
        for reserved, has_parent, next_step in (
                (True, True, "[LINK PLACEHOLDER: Send to the Board]"),
                (False, True, "Ask an LO Coordinator to reseat the Circle before the claim is tried again."),
                (False, False, "Follow the LO’s existing Coordinator recovery process.")):
            wording = commands._expired_capacity_message(deadline, reserved, has_parent, claim=True)
            self.assertTrue(wording.startswith("You could not claim this work."))
            for part in (stamp, "This attempt left the work assignment unchanged.", next_step):
                self.assertIn(part, wording)
        # The actual LO command joins and takes the office. An exact retry returns the same key.
        parent_request = fixture.args | {"circle_uuid": circle.circle_uuid, "request_uuid": uuid4()}
        parent_office = commands.recover_circle(**parent_request)
        self.assertEqual(commands.recover_circle(**parent_request), parent_office)
        self.assertEqual(commands.operating_status(**allowed), "ACTIVE")
        with self.assertRaisesMessage(places.CircleAccessRefused, "The Circle already has a Coordinator."):
            commands.recover_circle(**(parent_request | {"request_uuid": uuid4()}))
        self.assertEqual(models.CircleStateEvent.objects.filter(circle=circle, action="RESEAT").count(), 1)

    def test_member_exit_settles_both_occurrences_before_its_write(self):
        """W4 action settlement. Both the nonfinal leave and composed final exit retain their real enterer."""
        from src.intevia.services.organism_membership_service import OrganismMembershipService
        from tests.test_c1b_departure import DepartureTests

        fixture, direct = form_with_members()
        composed = fixture.form()
        departing = fixture.fx._member()
        living = fixture.seat(direct, fixture.fx._bystander())
        final = fixture.seat(composed, departing)
        first = fixture.seat(direct, fixture.fx._bystander())
        losses = {}
        for target in (direct, composed):
            founder = models.CircleParticipation.objects.get(circle=target, membership=fixture.fx.founder_member)
            losses[target.pk] = leave(fixture, target, founder)
        clock.advance_to(max(loss.effective_at for loss in losses.values()) + timedelta(hours=720, microseconds=1))
        labels = ("OPEN_VACANCY", "BECOME_DORMANT")
        for target in (direct, composed):
            self.assertFalse(models.CircleStateEvent.objects.filter(circle=target, action__in=labels).exists())
        self.assertEqual(circle_receipts(fixture), [])
        own = dict(credential=first.membership.identity.credential, organism_uuid=fixture.lo.organism_id,
                   circle_uuid=direct.circle_uuid, participation_uuid=first.participation_uuid,
                   confirmed=True, request_uuid=uuid4())
        try:
            left = commands.leave(**own)
        except DatabaseError as error:
            if "CIRCLE_DORMANCY_OWED" not in str(error):
                raise
            self.fail("A genuine exit must settle both occurrences before its own write.")
        self.assertEqual(commands.leave(**own).pk, left.pk)
        self.assertFalse(models.CircleParticipationEnd.objects.filter(participation=living).exists())
        route = OrganismMembershipService(authority=fixture.fx.authority, clock=support.database_now)
        due = losses[composed.pk].effective_at + timedelta(hours=720)
        earlier_knowledge = support.database_now()
        command = DepartureTests().command(departing, effective_at=due, occurred_at=due)
        try:
            route.leave_organism(command)
        except DatabaseError as error:
            if not any(code in str(error) for code in ("CIRCLE_DORMANCY_OWED", "CIRCLE_DISSOLUTION_HISTORY",
                                                       "CIRCLE_DISSOLUTION_SOURCE", "S015 chain:")):
                raise
            self.fail("A final LO departure effective at the deadline must settle both facts and commit CLOSED.")
        self.assertEqual(models.CircleParticipationEnd.objects.filter(participation=final).count(), 1)
        for target, action in ((direct, "LEAVE_CIRCLE"), (composed, "DISSOLVE_CIRCLE")):
            loss = losses[target.pk]
            chain = models.CircleStateEvent.objects.filter(circle=target).order_by("sequence")
            self.assertEqual(list(chain.filter(action__in=labels).values_list("action", flat=True)), list(labels),
                             "Each real exit must settle precisely its opening and dormancy.")
            opening, dormancy = list(chain.filter(action__in=labels))
            act = chain.last()
            self.assertEqual((opening.action, dormancy.action, act.action), (*labels, action))
            self.assertLess(opening.sequence, dormancy.sequence)
            self.assertLess(dormancy.sequence, act.sequence)
            self.assertEqual((opening.effective_at, dormancy.effective_at),
                             (loss.effective_at, loss.effective_at + timedelta(hours=720)))
            for fact in (opening, dormancy):
                self.assertEqual(fact.evidence_reference, f"vacancy:{loss.event_uuid}")
                enterer = departing.identity_id if target == composed else first.membership.identity_id
                self.assertEqual(circle_enterer(fact), enterer)
                self.assertGreater(fact.recorded_at, dormancy.effective_at)
        exit_event = models.CircleStateEvent.objects.get(circle=composed, action="DISSOLVE_CIRCLE")
        tied = models.CircleStateEvent.objects.get(circle=composed, action="BECOME_DORMANT")
        actual_end = models.CircleParticipationEnd.objects.get(participation=final)
        self.assertEqual((exit_event.predecessor_id, exit_event.prior_state, exit_event.resulting_state),
                         (tied.pk, "DORMANT", "CLOSED"))
        self.assertEqual((exit_event.effective_at, exit_event.occurred_at, exit_event.received_at),
                         (due, due, actual_end.received_at))
        self.assertEqual((tied.effective_at, tied.occurred_at), (due, due))
        self.assertLess(exit_event.received_at, tied.received_at)
        self.assertLessEqual(tied.recorded_at, exit_event.recorded_at)
        with connection.cursor() as cur:
            after = support.database_now()
            for known, expected in ((earlier_knowledge, "ACTIVE"), (after, "CLOSED")):
                cur.execute("SELECT public.s015_fold_effective_state('core_circle',%s,%s,%s)", [composed.pk,due,known])
                self.assertEqual(cur.fetchone(), (expected,), "The later-known sourced closure wins the equal effective cut.")
        composed.refresh_from_db()
        self.assertEqual(composed.state, "CLOSED")
        self.assertEqual(circle_turn(fixture).result, "IDLE")
        self.assertEqual(circle_receipts(fixture), [], "An action-settled occurrence must never acquire a wake receipt.")

    def test_final_lo_departure_effective_before_deadline_closes_at_exit(self):
        """D-1. A late intake must preserve the earlier final exit."""
        from src.intevia.services.organism_membership_service import OrganismMembershipService
        from tests.test_c1b_departure import DepartureTests

        fixture, circle = form_with_members()
        member = fixture.fx._member()
        place = fixture.seat(circle, member)
        founder = models.CircleParticipation.objects.get(circle=circle, membership=fixture.fx.founder_member)
        loss = leave(fixture, circle, founder)
        known = support.database_now()
        vacancy = episode(circle, known, known)
        self.assertIsNotNone(vacancy, "The founder's departure must establish a recovery deadline.")
        cause, loss_at, deadline, status = vacancy
        self.assertEqual((cause, loss_at, status), (loss.event_uuid, loss.effective_at, "OPEN"),
                         "The deadline must belong to this Circle's still-open loss.")
        exit_at = deadline - timedelta(microseconds=1)
        self.assertLess(max(place.effective_at, loss_at), exit_at,
                        "The departing member must hold the place during the recovery period.")
        ends = models.CircleParticipationEnd.objects
        remaining = models.CircleParticipation.objects.filter(circle=circle).exclude(
            pk__in=ends.filter(participation__circle=circle).values("participation_id"))
        self.assertEqual(list(remaining.values_list("pk", flat=True)), [place.pk],
                         "Only the departing member's place may remain.")
        chain = models.CircleStateEvent.objects.filter(circle=circle).order_by("sequence")
        before = chain.last()
        clock.advance_to(deadline + timedelta(microseconds=1))
        self.assertGreater(support.database_now(), deadline, "The real intake must run after the deadline.")
        self.assertFalse(chain.filter(action__in=("OPEN_VACANCY", "BECOME_DORMANT")).exists(),
                         "The leave must settle a silent episode.")
        route = OrganismMembershipService(authority=fixture.fx.authority, clock=support.database_now)
        command = DepartureTests().command(member, effective_at=exit_at, occurred_at=exit_at)
        try:
            first = route.leave_organism(command)
        except DatabaseError as error:
            if getattr(getattr(error.__cause__, "diag", None), "message_primary", None) != "CIRCLE_DISSOLUTION_HISTORY":
                raise
            self.fail("CIRCLE_DISSOLUTION_HISTORY: the final exit before the deadline must commit at its own date.")

        member.refresh_from_db()
        transition = member.head_transition
        self.assertEqual((member.state, transition.resulting_state, transition.lineage_reference),
                         ("ENDED", "ENDED", first.lineage_reference),
                         "The returned leave must identify the committed membership ending.")
        self.assertGreater(transition.received_at, deadline, "The membership must retain its late intake.")
        times = (exit_at, exit_at, transition.received_at)
        self.assertEqual((transition.effective_at, transition.occurred_at, transition.received_at), times,
                         "The membership must end at the requested earlier instant.")
        place_ends = list(ends.filter(participation=place))
        self.assertEqual(len(place_ends), 1, "Exactly one composed ending must remove the final place.")
        end = place_ends[0]
        self.assertEqual((end.composed_from_event_uuid, end.enterer_identity_id, end.entry_mode),
                         (transition.event_uuid, member.identity_id, "COMPOSED"),
                         "The place must end through this member's recorded departure.")
        self.assertEqual((end.effective_at, end.occurred_at, end.received_at), times,
                         "The place ending must preserve the exit and its receipt.")
        new_events = list(chain.filter(sequence__gt=before.sequence))
        self.assertEqual([event.action for event in new_events], ["OPEN_VACANCY", "DISSOLVE_CIRCLE"],
                         "Only the vacancy opening and the final closure belong on this chain.")
        opening, closure = new_events
        self.assertEqual((opening.effective_at, opening.occurred_at, opening.evidence_reference),
                         (loss_at, loss_at, f"vacancy:{cause}"), "The opening must cite and date the actual loss.")
        self.assertGreaterEqual(opening.received_at, transition.received_at,
                                "The opening's own receipt must be at or after intake.")
        self.assertEqual((circle_enterer(opening), circle_enterer(closure)), (member.identity_id, member.identity_id),
                         "The departing member must remain the enterer of both facts.")
        self.assertEqual((opening.predecessor_id, closure.predecessor_id, closure.prior_state,
                          closure.resulting_state, closure.evidence_reference),
                         (before.pk, opening.pk, "ACTIVE", "CLOSED",
                          f"last-place:{place.participation_uuid}|cause:{transition.event_uuid}"),
                         "Closure must follow the opening and cite this final place and its membership ending.")
        self.assertEqual((closure.effective_at, closure.occurred_at, closure.received_at), times,
                         "Closure must use the final place's effective, occurred and received times.")

        self.assertEqual(route.leave_organism(command), first, "An exact retry must return the first departure.")
        self.assertEqual(circle_turn(fixture).result, "IDLE", "A later Circle turn must find no occurrence to open.")
        self.assertEqual(circle_receipts(fixture), [], "The leave and later turn must leave no Circle wake receipt.")
        self.assertEqual(list(chain.filter(sequence__gt=before.sequence).values_list("pk", flat=True)),
                         [opening.pk, closure.pk], "Replay and the later turn must append no fact after closure.")
        self.assertEqual(list(ends.filter(participation=place).values_list("pk", flat=True)), [end.pk],
                         "Replay must keep precisely the original place ending.")
        member.refresh_from_db()
        circle.refresh_from_db()
        self.assertEqual((member.head_transition_id, circle.state, circle.head_event_id),
                         (transition.pk, "CLOSED", closure.pk), "The original ending and closure must remain the heads.")


def circle_wake(fixture):
    person = fixture.actor
    return te.Wake(str(fixture.lo.organism_id), person.pk, person.credential_id, person.access_epoch)


def circle_turn(fixture):
    election_support.idle_until_turn_of(fixture.lo, fixture.actor, "CIRCLE")
    return te.run(circle_wake(fixture), alias="default")


def circle_receipts(fixture):
    with connection.cursor() as cursor:
        cursor.execute("""SELECT source_ref,occurrence,effect_ref,due_at,effect_at,discovered_at,enterer_id,
                          outcome,rule_ref FROM public.core_tereceipt WHERE organism_id=%s AND consumer='CIRCLE'
                          ORDER BY discovered_at,receipt_uuid""", [fixture.lo.pk])
        return cursor.fetchall()
