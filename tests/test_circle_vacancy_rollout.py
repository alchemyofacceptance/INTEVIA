"""W10. Migrate lawful old Circles and refuse an inconsistent population without a partial rollout."""

import json
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from hashlib import sha256
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from core import models
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from intevia.test_postgresql_backend import clock
from src.intevia.services import circle_commands as commands
from src.intevia.services import circle_participation as places
from src.intevia.services import circle_work
from tests import s015_0021_support as support
from tests.test_circle_vacancy_episode import (
    episode,
    form_with_members,
    leave,
    migration,
)

PREVIOUS = "0104_clock_automatic_time_fields"
HEAD = "0105_circle_vacancy_episode"


def migrate(name):
    MigrationExecutor(connection).migrate([("core", name)])


def migration_footprint(circles, cut):
    """Read independently of the failed executor, including the actual ledger and immutable event facts."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT app,name FROM django_migrations ORDER BY app,name")
        ledger = tuple(cursor.fetchall())
        bodies = tuple(migration.definition(cursor, sig) for sig in
                       [migration.VACANT, migration.REDUCE, *(sig for sig, _ in migration.ADDITIONS)])
        per_circle = []
        for circle in circles:
            cursor.execute("SELECT head_event_id,state,current_state_effective_at,state_known_at,"
                           "state_source_event_set_fingerprint,public.circle_vacant(id,NULL,%s,%s) "
                           "FROM public.core_circle WHERE id=%s", [cut, cut, circle.pk])
            anchor = cursor.fetchone()
            cursor.execute("SELECT event_uuid,l1_commitment,l2_commitment,payload_fingerprint,effective_at,recorded_at "
                           "FROM public.core_circlestateevent WHERE circle_id=%s ORDER BY sequence", [circle.pk])
            events = cursor.fetchall()
            per_circle.append((anchor, events))
        return ledger, bodies, per_circle


def transcript(output):
    return json.loads(next(line for line in output.getvalue().splitlines() if line.startswith('{"recipient"')))


def exercise_legacy_cut(test, work, loss):
    """Use the installed fold text with its two Circle fact reads pointed at a rollback-only relation.

    The late work is an actual 0104 record. The ordered dormancy marker is a supplied fact. CB-2
    owns its writer. This exercises the reducer's dispatch and interval; it claims no settled act.
    """
    due = loss.effective_at + timedelta(hours=720)
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("""CREATE TEMP TABLE w10_facts ON COMMIT DROP AS
            SELECT id,circle_id,action,prior_state,resulting_state,effective_at,recorded_at,evidence_reference
            FROM public.core_circlestateevent WHERE id=%s""", [work.pk])
        raw = migration.definition(cur, migration.REDUCE)
        relation = "public.core_circlestateevent e"
        test.assertEqual(raw.count(relation), 2)
        cur.execute(raw.replace("FUNCTION public.s015_reduce_projection(", "FUNCTION pg_temp.w10_reduce(")
                    .replace(relation, "pg_temp.w10_facts e"))
        cur.execute("SELECT pg_temp.w10_reduce('core_circle',%s::bigint[])", [[work.pk]])
        test.assertEqual(cur.fetchone()[0], "ACTIVE", "Before dormancy is known the work retains its old reading.")
        cur.execute("""INSERT INTO pg_temp.w10_facts
            VALUES (%s,%s,'BECOME_DORMANT','ACTIVE','DORMANT',%s,public.intevia_clock_instant(),%s)""",
                    [-work.pk, work.circle_id, due, "vacancy:" + str(loss.event_uuid)])
        ids = [-work.pk, work.pk]
        cur.execute("SELECT pg_temp.w10_reduce('core_circle',%s::bigint[])", [ids])
        test.assertEqual(cur.fetchone()[0], "DORMANT", "Old work must not reactivate a settled Circle.")
        # A later ordinary write cannot borrow the legacy exception. Equal recording times also fail closed.
        cur.execute("UPDATE pg_temp.w10_facts SET recorded_at=(SELECT recorded_at FROM pg_temp.w10_facts "
                    "WHERE id=%s) WHERE id=%s", [-work.pk, work.pk])
        cur.execute("SELECT pg_temp.w10_reduce('core_circle',%s::bigint[])", [ids])
        test.assertEqual(cur.fetchone()[0], "ACTIVE")
        transaction.set_rollback(True)


class CircleVacancyRolloutTests(TransactionTestCase):
    def test_populated_rollout_preserves_dates_or_refuses_the_whole_step(self):
        """W10. One rollout can distinguish a genuine old loss from contradictory historic evidence."""
        support._require_disposable_fixture_database()
        migrate(PREVIOUS)
        self.addCleanup(migrate, "0109_committee_chair_reservation")
        # The fixture writes under 0104, before the settlement writer exists.
        with patch.object(commands, "settle_vacancy", return_value=None):
            old, expired = form_with_members()
            fresh = old.form()
            volunteer = old.seat(expired, old.fx._bystander())
            replacement = old.seat(fresh, old.fx._bystander())
            old.seat(fresh, old.fx._bystander())
            expired_loss = leave(old, expired, models.CircleParticipation.objects.get(circle=expired,
                                 membership=old.fx.founder_member))
            recovered_loss = leave(old, fresh, models.CircleParticipation.objects.get(circle=fresh,
                                   membership=old.fx.founder_member))
            recovery_uuid = commands.recover_circle(credential=replacement.membership.identity.credential,
                                                    organism_uuid=old.lo.organism_id, circle_uuid=fresh.circle_uuid,
                                                    request_uuid=uuid4())
            period = timedelta(hours=720)
            due = expired_loss.effective_at + period
            clock.advance_to(due + timedelta(microseconds=1))
            # Produce an authentic late, neutral record under 0104's interim law.
            key = uuid4()
            title, role = "Index the collection", "service:library:cataloguer@1"
            ref = circle_work.opened_reference(title, role)
            old.grant(expired.circle_uuid, f"OPEN_WORK:{key}:" + sha256(ref.encode()).hexdigest())
            circle_work.open_work(**old.args, circle_uuid=expired.circle_uuid, work_uuid=key,
                                  title=title, role_reference=role)
            task = models.CircleStateEvent.objects.get(event_uuid=key)
            self.assertGreater(task.effective_at, due)
            fresh_loss = leave(old, fresh, replacement)
            self.assertNotEqual(recovered_loss.event_uuid, fresh_loss.event_uuid)
            self.assertTrue(models.ContextualRoleAssignment.objects.filter(assignment_uuid=recovery_uuid).exists())

        cut = support.database_now()
        targets = (expired, fresh)
        before = migration_footprint(targets, cut)
        for anchor, _ in before[2]:
            self.assertTrue(anchor[-1], "0104 regards both as physical vacancies.")
        # Add a second END for a single tenure only in the rollout query's input.
        # The population and classification predicate are the actual migration's.
        base_sql = migration.ROLLOUT_ROWS
        first = "ends AS MATERIALIZED ("
        second = " SELECT v.circle_uuid,v.organism_id,"
        self.assertEqual((base_sql.count(first), base_sql.count(second)), (1, 1))
        # Poison the last Circle in the report, after one classifiable Circle.
        target = max(targets, key=lambda c: c.circle_uuid)
        injected = base_sql.replace(first, "real_ends AS MATERIALIZED (").replace(second, """
         , ends AS (SELECT * FROM real_ends UNION ALL
           SELECT e.circle_id,e.office,e.event_uuid,e.sequence+1,%(cut)s,e.resulting_state
           FROM real_ends e WHERE circle_id=(SELECT id FROM vacant ORDER BY circle_uuid DESC LIMIT 1))
        """ + second)
        output = StringIO()
        with (
            patch.object(migration, "ROLLOUT_ROWS", injected),
            redirect_stdout(output),
            self.assertRaisesMessage(RuntimeError, "cannot classify"),
        ):
            migrate(HEAD)
        connection.close()  # Inspect the committed state from a fresh PostgreSQL session.
        self.assertEqual(migration_footprint(targets, cut), before, "Refusal must roll back the entire 0105.")
        report = transcript(output)
        self.assertEqual(report["recipient"], "Carmian Owen, Human Governor")
        self.assertEqual(report["result"], "REFUSED")
        bad = next(r for r in report["circles"] if r["circle"] == str(target.circle_uuid))
        self.assertEqual(bad["lo"], str(target.parent_organism.organism_id))
        self.assertEqual(bad["checked_cut"], report["checked_cut"])
        self.assertEqual(bad["classification"], "CANNOT_CLASSIFY")
        self.assertIn("two different effective END times", bad["evidence_gap"])

        output = StringIO()
        with redirect_stdout(output):
            migrate(HEAD)
        report = transcript(output)
        self.assertEqual(report["result"], "CLASSIFIED")
        checked = datetime.fromisoformat(report["checked_cut"])
        for circle, cause, status in ((expired, expired_loss, "EXPIRED"), (fresh, fresh_loss, "OPEN")):
            with self.subTest(status=status):
                row = next(r for r in report["circles"] if r["circle"] == str(circle.circle_uuid))
                self.assertEqual((row["cause"], row["classification"]), (str(cause.event_uuid), status))
                self.assertEqual(datetime.fromisoformat(row["loss"]), cause.effective_at)
                self.assertEqual(datetime.fromisoformat(row["deadline"]), cause.effective_at + period)
                self.assertEqual(episode(circle, checked, checked),
                                 (cause.event_uuid, cause.effective_at, cause.effective_at + period, status))
        no_proof = migration.rollout_classification((expired.circle_uuid, old.lo.organism_id, None, None,
                                                    None, None, None), checked)
        self.assertEqual((no_proof["classification"], no_proof["deadline"]), ("NO_PROVABLE_LOSS", None))
        self.assertTrue(no_proof["evidence_gap"])
        after = migration_footprint(targets, cut)
        for (anchor, events), (new_anchor, new_events) in zip(before[2], after[2], strict=True):
            self.assertEqual((new_anchor[:-1], new_events), (anchor[:-1], events))
        with connection.cursor() as cur:
            cur.execute("SELECT public.s015_fold_effective_state('core_circle',%s,%s,%s), "
                        "public.circle_effective_state(%s,%s,%s)", [expired.pk, checked, checked] * 2)
            self.assertEqual(cur.fetchone(), ("ACTIVE", "DORMANT"))
        with self.assertRaises(places.CircleAccessRefused):
            commands.recover_circle(credential=volunteer.membership.identity.credential,
                                    organism_uuid=old.lo.organism_id, circle_uuid=expired.circle_uuid,
                                    request_uuid=uuid4())
        self.assertEqual(migration_footprint(targets, cut), after)
        exercise_legacy_cut(self, task, expired_loss)

    test_populated_rollout_preserves_dates_or_refuses_the_whole_step.ISOLATION_FULL_CAPTURE = True
