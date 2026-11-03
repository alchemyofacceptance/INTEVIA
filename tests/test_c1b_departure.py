"""C1b departures on the owned clone. Production provider, real office decisions."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import (
    command_intake,
    organism_membership_service as memberships,
)
from src.intevia.services.organism_command_support import S015CommandReplayMismatch
from tests import s015_0021_support as fixtures
from tests import test_sr_office_actions as office
from tests.test_c1_rejoin import _attempt


class DepartureTests(TransactionTestCase):
    def setUp(self):
        self.fx = office.SROfficeActionTests()
        self.fx._start_organism()
        self.service = memberships.OrganismMembershipService(
            authority=self.fx.authority, clock=fixtures.database_now
        )

    def command(self, member, **changes):
        at = fixtures.database_now()
        command = memberships.LeaveOrganismCommand(
            credential=member.identity.credential,
            membership_uuid=member.membership_uuid,
            confirmed=True,
            reason_class="VOLUNTARY",
            occurred_at=at,
            effective_at=at,
            idempotency_key=uuid4().hex,
            request_reference="c1b:" + uuid4().hex,
        )
        return replace(command, **changes)

    def test_confirmation_is_bound_to_the_request(self):
        member = self.fx._member()
        command = self.command(member, confirmed=False)
        for cmd in (
            command,
            memberships.TransitionMembershipCommand(
                credential=command.credential,
                membership_uuid=member.membership_uuid,
                resulting_state="ENDED",
                reason_class="VOLUNTARY",
                occurred_at=command.occurred_at,
                effective_at=command.effective_at,
                idempotency_key=uuid4().hex,
                request_reference=uuid4().hex,
            ),
        ):
            error, _ = _attempt(lambda: self.service.transition_membership(cmd))
            self.assertIs(
                type(error),
                memberships.S015MembershipEdgeRefused,
                "An unconfirmed request must explicitly refuse leaving.",
            )
        member.refresh_from_db()
        self.assertEqual(
            member.state, "ACTIVE", "An unconfirmed departure must preserve membership."
        )
        good = self.command(member)
        outcome = self.service.leave_organism(good)
        self.assertEqual(
            self.service.leave_organism(good),
            outcome,
            "Replay must return the recorded departure.",
        )
        error, _ = _attempt(
            lambda: self.service.leave_organism(replace(good, confirmed=False))
        )
        self.assertIs(
            type(error),
            S015CommandReplayMismatch,
            "Removing confirmation must change the intake fingerprint.",
        )

    def _withdraw_standing_after_admission(self, person, assignment):
        command = self.command(person)
        admit = command_intake.admit_request
        observed = []

        def admit_then_withdraw(*args):
            bound = admit(*args)
            if args[1] is not models.IntakeEntry.TRANSITION_MEMBERSHIP:
                return bound
            assignment.refresh_from_db()
            observed.append((bound.intake_id, assignment.state))
            # The Coordinator lawfully ends the ordinary office between intake and effect.
            self.fx.roles.transition_assignment(
                replace(self.fx._transition_command(assignment, "END"), effective_at=command.effective_at)
            )
            return bound

        with patch.object(command_intake, "admit_request", admit_then_withdraw):
            error, _ = _attempt(lambda: self.service.leave_organism(command))
        self.assertTrue(
            observed and observed[0][0] and observed[0][1] == "ACTIVE",
            "The actual intake must commit while the person still holds the office.",
        )
        self.assertIs(
            type(error),
            memberships.S015MembershipEdgeRefused,
            "Changed office authority after receipt must refuse the stale departure.",
        )
        self.assertIn(
            "office changed after",
            str(error),
            "The service must distinguish the intervening office ending from a date error.",
        )
        person.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(
            (person.state, assignment.state),
            ("ACTIVE", "ENDED"),
            "Only the separately authorised office ending may survive. Membership must remain active.",
        )
        self.assertEqual(
            models.ContextualRoleAssignmentStateEvent.objects.filter(
                assignment=assignment, action="END"
            ).count(),
            1,
            "A stale departure must not produce a second ending for the office.",
        )
        withdrawal = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=assignment, action="END")
        intake = models.CommandIntake.objects.get(pk=observed[0][0])
        self.assertGreater(withdrawal.recorded_at, intake.received_at,
                           "The independent office ending must be recorded after actual admission.")
        self.assertFalse(
            models.OrganismCommandReceipt.objects.filter(
                request_reference=command.request_reference
            ).exists(),
            "A refused departure cannot leave a success receipt.",
        )

    def test_consequences_rollback_together_then_commit(self):
        member = self.fx.founder_member
        _, definition = self.fx._define()
        _, assignment = self.fx._nominate(definition, member)
        self.fx.roles.transition_assignment(
            self.fx._transition_command(assignment, "ASSIGN")
        )
        seat, _ = self.fx._seat_founder()
        participation = models.CircleParticipation.objects.get(
            participation_uuid=seat.participation_uuid
        )
        command = self.command(member)
        before = self.fx._snapshot()
        original = (
            memberships.OrganismMembershipService._compose_current_participation_endings
        )

        def break_after_seat(service, **kwargs):
            original(service, **kwargs)
            raise ValueError("C1b witness stops after the Circle ending")

        with patch.object(
            memberships.OrganismMembershipService,
            "_compose_current_participation_endings",
            break_after_seat,
        ):
            failure, _ = _attempt(lambda: self.service.leave_organism(command))
        self.assertEqual(
            str(failure),
            "C1b witness stops after the Circle ending",
            "The rollback witness must reach the final Circle write.",
        )
        after = self.fx._snapshot()
        self.assertEqual(
            {k: v for k, v in after.items() if k != "CommandIntake"},
            {k: v for k, v in before.items() if k != "CommandIntake"},
            "Late failure must roll back offices, membership, Circle ending, decisions and receipt.",
        )
        error, _ = _attempt(lambda: self.service.leave_organism(command))
        self.assertIsNone(
            error,
            f"Retry after rollback must complete every consequence. Got {type(error).__name__}.",
        )
        member.refresh_from_db()
        self.fx.organism.refresh_from_db()
        self.assertEqual(
            (member.state, self.fx.organism.state),
            ("ENDED", "CLOSED"),
            "Leaving the last membership must dissolve its organisation in the same transaction.",
        )
        end = models.CircleParticipationEnd.objects.get(participation=participation)
        self.assertEqual(
            end.composed_from_event_uuid,
            member.head_transition.event_uuid,
            "The Circle ending must retain the exact causal membership event.",
        )
        events = models.ContextualRoleAssignmentStateEvent.objects.filter(
            assignment__membership=member, action="END"
        )
        self.assertEqual(
            events.count(),
            2,
            "Both the ordinary office and founding Coordinator office must end.",
        )
        for event in events:
            decision = models.AuthorityDecision.objects.get(
                decision_reference=event.authority_decision_reference
            )
            self.assertEqual(
                (
                    decision.outcome,
                    decision.action,
                    decision.target["assignment_uuid"],
                    decision.target["departure_event"],
                    event.evidence_reference,
                ),
                (
                    "QUALIFIED",
                    "TRANSITION_CONTEXTUAL_ROLE",
                    str(event.assignment.assignment_uuid),
                    str(member.head_transition.event_uuid),
                    decision.authority_reference,
                ),
                "Each office needs its own qualified decision naming the assignment and causal departure.",
            )

    def test_probationer_counts_for_essential_coverage(self):
        remaining = self.fx._member()
        self._state(
            remaining,
            "core_organismmembership",
            "core_organismmembershiptransition",
            "PROBATIONARY",
        )
        error, _ = _attempt(
            lambda: self.service.leave_organism(self.command(self.fx.founder_member))
        )
        self.assertIs(
            type(error),
            memberships.S015MembershipEdgeRefused,
            "A probationer keeps the organisation in being.",
        )
        self.assertIn(
            "handover",
            str(error),
            "Loss of essential coverage must explain the available handover.",
        )
        self.fx.organism.refresh_from_db()
        self.assertEqual(
            self.fx.organism.state,
            "ACTIVE",
            "An organisation with a probationary member cannot dissolve.",
        )

    def _state(self, anchor, token, table, state):
        at = fixtures.database_now()
        with transaction.atomic(), connection.cursor() as cursor:
            model = (
                models.LivingOrganismEvent
                if token == "core_livingorganism"
                else models.OrganismMembershipTransition
            )
            fk = (
                {"living_organism": anchor}
                if token == "core_livingorganism"
                else {"membership": anchor}
            )
            head = model.objects.filter(**fk).latest("sequence")
            extra = (
                {}
                if token == "core_livingorganism"
                else {"reason_class": "TEST_FIXTURE"}
            )
            fixtures.append_event(
                cursor,
                table,
                anchor.pk,
                head.sequence + 1,
                "TRANSITION",
                head.resulting_state,
                state,
                self.fx.actor.pk,
                self.fx.ids["basis"],
                uuid4().hex,
                pred_id=head.pk,
                eff=at,
                occ=at,
                rec=at,
                extra=extra,
            )
            fixtures.refresh(cursor, token, anchor.pk, at)

    def test_departure_before_seat_start_refuses(self):
        member = self.fx.founder_member
        seat, _ = self.fx._seat_founder()
        participation = models.CircleParticipation.objects.get(
            participation_uuid=seat.participation_uuid
        )
        boundary = participation.effective_at - timedelta(microseconds=1)
        error, _ = _attempt(
            lambda: self.service.leave_organism(
                self.command(member, effective_at=boundary)
            )
        )
        self.assertIs(
            type(error),
            memberships.S015MembershipRefused,
            "A departure one microsecond before a recorded seat starts must give the scheduled-seat refusal.",
        )
        self.assertEqual(error.refusal_code.value, "SBG_DEPARTURE_PRECEDES_PARTICIPATION",
                         "A premature ending must return the scheduled-seat refusal code.")
        self.assertFalse(
            models.CircleParticipationEnd.objects.filter(
                participation=participation
            ).exists(),
            "Refusal before a seat starts must retain that seat and its original history.",
        )

    def test_dormant_holder_can_leave_without_unpausing(self):
        _, definition = self.fx._define()
        member, assignment = self.fx._appoint(definition)
        self._withdraw_standing_after_admission(member, assignment)
        _, assignment = self.fx._appoint(definition, member)
        self._state(
            self.fx.organism,
            "core_livingorganism",
            "core_livingorganismevent",
            "DORMANT",
        )
        error, _ = _attempt(lambda: self.service.leave_organism(self.command(member)))
        self.assertIsNone(
            error,
            f"Dormancy must allow the holder's qualified departure. Got {type(error).__name__}.",
        )
        assignment.refresh_from_db()
        self.fx.organism.refresh_from_db()
        self.assertEqual(
            (assignment.state, self.fx.organism.state),
            ("ENDED", "DORMANT"),
            "Ending an ordinary office on departure must leave ordinary governance paused.",
        )
