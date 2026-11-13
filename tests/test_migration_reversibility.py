"""The standing reversibility check: every migration after a declared base can be undone and applied again.

The Human Governor added this check to TIMED EVENTS T1b on 12 Nov 2026 ("Let's get this done while we are
working on this."). A migration whose reverse fails was found only by the whole verification job; this witness
asks the question of every new migration, in about a second, at the first targeted run.

How to keep it:

- BASE names, for each app, the last migration this check does not unwind. It is moved only by a ruling.
  A migration added after it is unwound and applied again by this witness with no change here.
- A migration that cannot be reversed by design is named in EXEMPT with its reason and the ruling that
  allows it. No migration is exempt silently, and none without a ruling. An entry that is stale (it names
  no migration after the base) or not needed (the migration reverses) fails this witness. Where an
  exemption stands, the descent stops above it, and every migration left unexercised below it must be
  named in EXEMPT too.

- What this check blames for a failure, what it considers for an exemption and what it counts as undone are
  the executor's own reports of the migration it began and finished (descend, reapply). One step of the
  descent can reverse more than one migration where the graph branches, so the step asked for is never taken
  as the migration reversed. REV-02 holds that on a small branched graph kept in memory.

It takes no time from PostgreSQL's own functions and none from the host, and it never moves the clock.
"""

from django.db import connection
from django.db.migrations import Migration, RunPython
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase, override_settings

from core import clock_startup as startup
from intevia.test_postgresql_backend import clock as owned_route
from verification.isolation import state as instrument

# The base: main at b85df0db10ad3430a44b8947177df9829920853f, each app's last migration there.
# An app that is not named here did not exist at the base, and is unwound entirely.
BASE = {
    "admin": "0003_logentry_add_action_flag_choices",
    "auth": "0012_alter_user_first_name_max_length",
    "contenttypes": "0002_remove_content_type_name",
    "core": "0095_te_c2_join",
    "email_foundation": "0001_initial",
    "sessions": "0001_initial",
}
# Migrations that are not unwound: (app, name, the reason, the ruling that allows it). Empty: every migration
# after the base reverses.
EXEMPT = ()
PRODUCTION_SETTINGS = {"S015_TEST_ROUTE": None, "INTEVIA_TEST_CONTEXT": False}
COUNTER_FOUND = "the test route's relation public.intevia_test_clock_offset_us is present"


def _reported(start, success):
    """A progress receiver for one executor call, and the two lists it fills: the migrations the executor said it
    began, and those it said it finished, in its own order."""
    began, finished = [], []

    def progress(action, migration=None, fake=False):
        if action == start:
            began.append((migration.app_label, migration.name))
        elif action == success:
            finished.append((migration.app_label, migration.name))

    return progress, began, finished


def _failure(error):
    return "%s: %s" % (type(error).__name__, str(error).split("\n")[0])


def descend(new_executor, graph, after_base, exempt):
    """Unwind the migrations after the base through the executor, a step at a time in its own order.

    Returns what was undone, what could not be, the exemptions that were not needed, and the exemption the descent
    stopped at. Each is taken from the executor's own reports of the migration it began and finished reversing."""
    undone, not_undone, needless, stopped_at = [], [], [], None
    for key in after_base:
        if key in undone:
            continue
        progress, began, finished = _reported("unapply_start", "unapply_success")
        parents = sorted(parent.key for parent in graph.node_map[key].parents if parent.key[0] == key[0])
        failed = None
        try:
            new_executor(progress).migrate([(key[0], parents[-1][1] if parents else None)])
        except Exception as error:
            failed = error
        undone.extend(finished)
        needless.extend(done for done in finished if done in exempt)
        if failed is not None:
            unfinished = [migration for migration in began if migration not in finished]
            reversing = unfinished[-1] if unfinished else None
            if reversing is None:
                not_undone.append("the step that unwinds %s.%s failed before the executor began to reverse a migration: %s"
                                  % (key[0], key[1], _failure(failed)))
            elif reversing in exempt:
                stopped_at = reversing
            else:
                not_undone.append("%s.%s could not be undone: %s" % (reversing[0], reversing[1], _failure(failed)))
            break
    return undone, not_undone, needless, stopped_at


def reapply(new_executor, received):
    """Apply the received heads again. Returns None, or the failure in words that name the migration the executor
    had begun to apply."""
    progress, began, finished = _reported("apply_start", "apply_success")
    try:
        new_executor(progress).migrate(list(received))
    except Exception as error:
        unfinished = [migration for migration in began if migration not in finished]
        applying = "%s.%s" % unfinished[-1] if unfinished else "no migration (the executor had not begun to apply one)"
        return "%s could not be applied again: %s" % (applying, _failure(error))
    return None


def restore_failure(not_undone, not_applied):
    """The failure to report when the received heads could not be applied again, or None. Where the descent had
    already stopped at a reverse that failed, it names both: what could not be undone, and what could not be applied
    again."""
    if not not_applied:
        return None
    before = ("the descent had already stopped (%s); then " % "; ".join(not_undone)) if not_undone else "after the descent "
    return ("REV-01: %sthe received migrations could not be applied again: %s. This clone is left below its head and "
            "must be destroyed." % (before, not_applied))


def real_executor(progress):
    return MigrationExecutor(connection, progress_callback=progress)


BRANCHED_APP = "rev02_branched"
BRANCHED = (("0001_root", ()), ("0002_left", ("0001_root",)), ("0002_right", ("0001_root",)), ("0003_merge", ("0002_left", "0002_right")))


class _MemoryLedger:
    """What the executor asks of its migration ledger, held in memory. REV-02 writes nothing to the database."""

    def __init__(self, applied):
        self.applied = set(applied)

    def ensure_schema(self):
        pass

    def applied_migrations(self):
        return dict.fromkeys(self.applied)

    def record_applied(self, app, name):
        self.applied.add((app, name))

    def record_unapplied(self, app, name):
        self.applied.discard((app, name))


class _MemoryExecutor(MigrationExecutor):
    """Django's own executor, unchanged, given a graph and a ledger held in memory in place of the ones it loads."""

    def __init__(self, graph, ledger, progress):
        loader = MigrationLoader(connection, load=False)
        loader.graph, loader.unmigrated_apps, loader.migrated_apps, loader.replacements = graph, set(), {BRANCHED_APP}, {}
        loader.disk_migrations = dict(graph.nodes)
        loader.applied_migrations = {key: graph.nodes[key] for key in ledger.applied}
        self.connection, self.loader, self.recorder, self.progress_callback = connection, loader, ledger, progress


def branched(reverse_fails=(), forward_fails=()):
    """A graph of an app that exists nowhere: a root, two siblings on it, and their merge. Each migration is one
    operation of Python that touches nothing; the named ones raise a generic error."""

    def step(name, failing):
        def run(apps, schema_editor):
            if name in failing:
                raise RuntimeError("%s was told to fail" % name)
        return run

    graph = MigrationGraph()
    for name, parents in BRANCHED:
        migration = Migration(name, BRANCHED_APP)
        migration.operations = [RunPython(step(name, forward_fails), step(name, reverse_fails))]
        migration.dependencies = [(BRANCHED_APP, parent) for parent in parents]
        graph.add_node((BRANCHED_APP, name), migration)
    for name, parents in BRANCHED:
        for parent in parents:
            graph.add_dependency(graph.nodes[(BRANCHED_APP, name)], (BRANCHED_APP, name), (BRANCHED_APP, parent))
    graph.validate_consistency()
    graph.ensure_not_cyclic()
    return graph


class MigrationReversibilityTests(TransactionTestCase):
    def test_every_migration_after_the_base_can_be_undone_and_reapplied(self):
        """REV-01. Every migration after the declared base can be unapplied and applied again through Django's
        migration executor on an owned clone, which returns to the state it had, at offset zero, and which
        production's startup refuses throughout."""
        executor = MigrationExecutor(connection)
        graph = executor.loader.graph
        received = tuple(graph.leaf_nodes())
        apps = sorted({app for app, _name in graph.nodes})
        absent = sorted(key for key in BASE.items() if key not in graph.nodes)
        self.assertEqual(absent, [], "REV-01 base: every migration the base names must exist.")
        targets = [(app, BASE.get(app)) for app in apps]
        plan = executor.migration_plan(targets)
        after_base = [(migration.app_label, migration.name) for migration, _backwards in plan]
        self.assertEqual([key for (_migration, backwards), key in zip(plan, after_base) if not backwards], [],
                         "REV-01 base: this clone must be at or past the base in every app, so that the plan only unwinds.")
        self.assertGreater(len(after_base), 0, "REV-01 base: there must be a migration after the base to unwind, or the base has passed the head.")

        exempt = {(app, name): (reason, ruling) for app, name, reason, ruling in EXEMPT}
        self.assertEqual(len(exempt), len(EXEMPT), "REV-01 exemptions: no migration may be named twice.")
        self.assertEqual(sorted(key for key in exempt if key not in after_base), [],
                         "REV-01 exemptions: an entry must name a migration that lies after the base. A stale entry is removed.")
        self.assertEqual(sorted(key for key, words in exempt.items() if not all(isinstance(word, str) and word.strip() for word in words)), [],
                         "REV-01 exemptions: every entry must give its reason and the ruling that allows it.")
        without_reverse = [key for (migration, _backwards), key in zip(plan, after_base)
                           if key not in exempt and not all(operation.reversible for operation in migration.operations)]
        self.assertEqual(without_reverse, [],
                         "REV-01: every operation of a migration after the base must declare a reverse. A migration that cannot be "
                         "reversed by design is named in EXEMPT with its reason and its ruling.")

        def ledger():
            return sorted(MigrationRecorder(connection).applied_migrations())

        def described():
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT p.oid::regprocedure::text, pg_catalog.obj_description(p.oid, 'pg_proc')
                         FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'public' AND pg_catalog.obj_description(p.oid, 'pg_proc') IS NOT NULL ORDER BY 1""")
                return cursor.fetchall()

        def production_startup(label):
            with override_settings(**PRODUCTION_SETTINGS):
                with self.assertRaises(startup.ClockStartupRefused,
                                       msg="REV-01 %s: production's startup must refuse this clone." % label) as refused:
                    startup.refuse_unless_production_clock("default")
            return str(refused.exception)

        before, ledger_before, described_before = instrument.capture(), ledger(), described()
        counter_before = before["intevia_clock_counter"]
        self.assertEqual(instrument.clock_counter_violations(counter_before, instrument.clock_app_applied(before["migrations"])), [],
                         "REV-01: the clone must begin with its clock at zero.")
        self.assertIn(COUNTER_FOUND, production_startup("at head"), "REV-01 at head: the refusal must name the counter.")

        restored = []

        def restore():
            """Apply the received heads again, once. None, or the failure in words that name the migration."""
            if restored:
                return None
            restored.append(True)
            return reapply(real_executor, received)

        def restore_at_cleanup():
            not_applied = restore()
            if not_applied:
                raise AssertionError("REV-01: %s. This clone is left below its head and must be destroyed." % not_applied)

        self.addCleanup(restore_at_cleanup)
        # Down, a step at a time in the executor's own order. What is blamed, excused and counted is what the
        # executor reports it began and finished reversing (descend), so that a reverse that fails is named rightly.
        undone, not_undone, needless, stopped_at = descend(real_executor, graph, after_base, exempt)
        left = [key for key in after_base if key not in undone]
        at_the_base = (ledger(), instrument.clock_counter_observation(), production_startup("at the lowest point")) if not not_undone else None
        failed_again = restore_failure(not_undone, restore())
        if failed_again:
            self.fail(failed_again)

        self.assertEqual(not_undone, [], "REV-01: every migration after the base must be undone through the executor. The clone was restored.")
        self.assertEqual(needless, [], "REV-01 exemptions: an entry whose migration was undone is not needed, and is removed.")
        self.assertEqual([key for key in left if key not in exempt], [],
                         "REV-01 exemptions: a migration left unexercised below an exemption (%r) must be named in EXEMPT too." % (stopped_at,))
        ledger_low, counter_low, refusal_low = at_the_base
        self.assertEqual([key for key in undone if key in ledger_low], [], "REV-01 at the lowest point: the ledger must hold no migration that was undone.")
        self.assertEqual(sorted(set(ledger_before) - set(ledger_low)), sorted(undone),
                         "REV-01 at the lowest point: the ledger must have lost the undone migrations and no other.")
        self.assertEqual(counter_low, counter_before, "REV-01 at the lowest point: the clock's counter must be kept, at zero and exactly as it was.")
        self.assertIn(COUNTER_FOUND, refusal_low, "REV-01 at the lowest point: production's refusal must name the counter.")

        after = instrument.capture()
        self.assertEqual(instrument.compare(before, after), ([], []), "REV-01 restored: the full capture must equal the one taken at head.")
        self.assertEqual(after["intevia_clock_counter"], counter_before, "REV-01 restored: the counter must be the one kept, at zero.")
        self.assertEqual(ledger(), ledger_before, "REV-01 restored: the ledger must be as it was received.")
        self.assertEqual(described(), described_before, "REV-01 restored: every routine's comment must be as it was.")
        self.assertEqual(owned_route.offset(), 0, "REV-01 restored: the clock must be at zero.")
        self.assertIn(COUNTER_FOUND, production_startup("restored"), "REV-01 restored: production's startup must still refuse.")

    def test_the_descent_names_the_migration_the_executor_was_reversing(self):
        """REV-02. Where the graph branches, one step of the descent reverses more than one migration. The failure is
        laid on the migration the executor had begun to reverse, an exemption is decided on that same migration, what
        is counted as undone is what the executor finished, and a failed re-apply names the migration being applied;
        and a restore that fails after a descent that could not undo a migration names both."""
        root, left, right, merge = ((BRANCHED_APP, name) for name, _parents in BRANCHED)
        self.assertEqual([key for key in (root, left, right, merge) if key[0] in {app for app, _name in MigrationExecutor(connection).loader.graph.nodes}],
                         [], "REV-02: the branched graph must belong to no app of this project.")
        ruling = ("a reason", "a ruling")

        def descent(reverse_fails, exempt):
            graph, held = branched(reverse_fails=reverse_fails), _MemoryLedger((root, left, right, merge))

            def new_executor(progress):
                return _MemoryExecutor(graph, held, progress)

            plan = new_executor(None).migration_plan([(BRANCHED_APP, root[1])])
            after_base = [(migration.app_label, migration.name) for migration, _backwards in plan]
            return after_base, descend(new_executor, graph, after_base, exempt), sorted(held.applied)

        after_base, outcome, held = descent((), {})
        self.assertEqual((after_base, outcome, held), ([merge, left, right], ([merge, left, right], [], [], None), [root]),
                         "REV-02 shape: the plan must unwind the merge, then the first sibling, then the second; with no failure "
                         "all three are counted as undone, from the executor's reports, and the ledger holds the root alone.")
        named = "%s.%s could not be undone: RuntimeError: %s was told to fail" % (right[0], right[1], right[1])
        with self.subTest(failing="the second sibling's reverse, in the step asked for the first"):
            _after, outcome, held = descent({right[1]}, {})
            self.assertEqual((outcome, held), (([merge, left], [named], [], None), [root, right]),
                             "REV-02: the step that unwinds the first sibling reverses both; the second's failure must be laid on "
                             "the second, and the first, which the executor did reverse, must be counted as undone, as the ledger shows.")
        with self.subTest(exempt="the second sibling, whose reverse fails"):
            _after, outcome, held = descent({right[1]}, {right: ruling})
            self.assertEqual((outcome, held), (([merge, left], [], [], right), [root, right]),
                             "REV-02: an exemption must be decided on the migration the executor was reversing: the descent stops at "
                             "the second sibling, and nothing is blamed.")
        with self.subTest(exempt="the first sibling, which reverses, while the second fails"):
            _after, outcome, held = descent({right[1]}, {left: ruling})
            self.assertEqual((outcome, held), (([merge, left], [named], [left], None), [root, right]),
                             "REV-02: an exemption for the step asked for must not excuse another migration's failure: the second "
                             "sibling is blamed, and the first's exemption is reported as not needed.")
        with self.subTest(failing="a sibling's forward, on applying again"):
            # Forwards the executor applies the second sibling first, so here the first sibling is the one that fails.
            graph, held = branched(forward_fails={left[1]}), _MemoryLedger((root,))
            words = reapply(lambda progress: _MemoryExecutor(graph, held, progress), [merge])
            self.assertEqual((words, sorted(held.applied)),
                             ("%s.%s could not be applied again: RuntimeError: %s was told to fail" % (left[0], left[1], left[1]), [root, right]),
                             "REV-02: a re-apply that fails must name the migration the executor had begun to apply, after the one "
                             "it did apply.")
            self.assertIsNone(reapply(lambda progress: _MemoryExecutor(branched(), held, progress), [merge]),
                              "REV-02: with nothing failing the same re-apply reports no failure.")
            self.assertEqual(sorted(held.applied), [root, left, right, merge], "REV-02: and the ledger then holds all four.")
        with self.subTest(failing="a reverse, and then the re-apply that follows it"):
            graph, held = branched(reverse_fails={right[1]}, forward_fails={left[1]}), _MemoryLedger((root, left, right, merge))

            def new_executor(progress):
                return _MemoryExecutor(graph, held, progress)

            _undone, not_undone, _needless, _stopped_at = descend(new_executor, graph, [merge, left, right], {})
            not_applied = reapply(new_executor, [merge])
            self.assertEqual(
                restore_failure(not_undone, not_applied),
                "REV-01: the descent had already stopped (%(app)s.0002_right could not be undone: RuntimeError: 0002_right was told to "
                "fail); then the received migrations could not be applied again: %(app)s.0002_left could not be applied again: "
                "RuntimeError: 0002_left was told to fail. This clone is left below its head and must be destroyed." % {"app": BRANCHED_APP},
                "REV-02: when the restore fails after a descent that could not undo a migration, the failure must name both.")
            self.assertEqual((restore_failure(not_undone, None), restore_failure([], not_applied)),
                             (None, "REV-01: after the descent the received migrations could not be applied again: %s.0002_left could not "
                                    "be applied again: RuntimeError: 0002_left was told to fail. This clone is left below its head and "
                                    "must be destroyed." % BRANCHED_APP),
                             "REV-02: a restore that succeeds reports nothing; one that fails after a whole descent names the re-apply alone.")
        self.assertEqual(owned_route.offset(), 0, "REV-02: the clock must be at zero.")
