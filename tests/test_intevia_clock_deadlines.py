"""A deadline reached by moving the INTEVIA Clock (TIMED EVENTS T1b, CLK-03).

A landed guard that refuses by a date must refuse at a date the clock was moved past exactly as it refuses at a date
that is really past: the same refusal code, the same words. This file holds the first such witness, and is the
pattern for the next: build the journey with the landed fixtures, show the act is accepted before the move (inside a
transaction that is rolled back, so that nothing stays), move the clock with no transaction open, and compare what the
guard then answers with what its landed real-time witness records.

This file moves the clock, so it takes no time from PostgreSQL's own functions and none from the host: every time it
uses is read through the INTEVIA Clock (``tests.s015_0021_support.database_now``). CLK-05's code half reads this file
and each definition it reaches, and fails if one of them reads the time any other way.
"""

from datetime import timedelta
from uuid import uuid4

from django.db import connection, transaction
from django.test import TransactionTestCase

from core import models
from intevia.test_postgresql_backend import clock as owned_route
from src.intevia.services import cq, elections
from tests import election_support as fx
from tests import s015_0021_support as support
from tests import test_law_acts as law

# How far ahead the witness sets its date. Test data only: it is not a period INTEVIA knows.
AHEAD = timedelta(hours=1)
# The refusal as the landed real-time witness records it (ELECT-13, tests/test_elections.py, "date outside seat
# term"), with the code the product gives those words (src/intevia/services/cq.py, MESSAGES).
RESIGNATION_DATE_REFUSED = (
    "ELECT_RESIGN_DATE",
    "This resignation date cannot be used. It must fall within your current term and not before your notice. "
    "Check the date the constitution gives.")


class _Undo(Exception):
    """Raised to end a block whose work must not stay."""


class ClockDeadlineTests(TransactionTestCase):
    def test_a_date_the_clock_has_passed_is_refused_as_a_date_really_past(self):
        """CLK-03. A Board member's dated resignation is refused by the landed guard (storage's
        ``elect_resignation_facts``, code ELECT_RESIGN_DATE) when its date is not usable. A date really past is refused
        with the code and words the landed witness records. A date one hour ahead is accepted; once the clock has been
        moved past that date, the same notice is refused with the same code and the same words, and nothing is
        recorded."""
        self.assertEqual(owned_route.offset(), 0, "CLK-03: the clock must start this witness at zero.")
        lo, founder = law.ordinary_birth(board_seats=2)
        scope = {"credential": founder.credential, "organism_uuid": lo.organism_id}
        seat = models.CircleParticipation.objects.get(circle__parent_organism=lo, membership__identity=founder)
        fx.enable_voting(scope)
        ahead = support.database_now() + AHEAD
        # Fixture dates in a single Article, never product intervals (as ELECT-13 writes them).
        clauses = {key: {"place": seat.seat_id, "effect": "DATED", "effective_at": value.isoformat()}
                   for key, value in (("Ahead", ahead), ("Past", seat.effective_at - timedelta(seconds=1)))}
        request = scope | fx.provision(founder, lo, {}, resignations=clauses) | {"participation_uuid": seat.participation_uuid}

        def answered(provision):
            """What the guard answers this notice: the refusal's code and words, or that it was accepted."""
            try:
                given = elections.resign_board_place(**request, provision=provision, request_uuid=uuid4())
            except cq.CQRefused as refused:
                return (refused.code, str(refused))
            return ("accepted", given["effect"])

        def recorded():
            return models.CircleParticipationEnd.objects.filter(participation=seat).count()

        really_past = answered("Past")
        self.assertEqual(really_past, RESIGNATION_DATE_REFUSED,
                         "CLK-03, a date really past: the guard must refuse it with the code and words its landed witness records.")
        # Before the move the date ahead is usable: the notice is accepted. It is rolled back, so that the same notice
        # can be given again after the move.
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS elect_vacancy_from_seat IMMEDIATE")
                before_the_move = answered("Ahead")
                raise _Undo()
        except _Undo:
            pass
        self.assertEqual((before_the_move, recorded()), (("accepted", "DATED"), 0),
                         "CLK-03, before the move: a date one hour ahead must be accepted, or this witness shows nothing; "
                         "and the rolled-back notice must leave no record.")
        moved = owned_route.advance_to(ahead + timedelta(seconds=1))
        self.assertGreater(moved, 0, "CLK-03: the clock must have moved.")
        self.assertGreater(support.database_now(), ahead, "CLK-03: after the move the clock must show a time past the date.")
        after_the_move = answered("Ahead")
        self.assertEqual(after_the_move, really_past,
                         "CLK-03, after the move: the guard must refuse a date the clock has passed exactly as it refuses a "
                         "date really past: the same code, the same words.")
        self.assertEqual(recorded(), 0, "CLK-03, after the move: a refused notice must record nothing.")
