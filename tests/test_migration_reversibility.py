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

CLK-08 is witnessed here too, since CK-2j1a (the ruled fold: the CK-2 commission's ruling on REV-01 and CLK-08,
and the Human Governor's decision of 14 Nov 2026 on CK-2i's test cost). Its two stops are two points of REV-01's one descent: the first point at which the
moving part is reversed while the clock's own migration (core 0096) is still applied, and the lowest point, where
both are reversed. The clone is therefore unwound and restored once for both claims. What CLK-08 looks at is taken
at each stop without asserting (clock_observation) and judged once the clone is restored, in a sub-test of its
own, so that each claim reports its own failure. At the first stop the storage conversions above 0096 may already
be reversed; CLK-08 looks only at the faces, the moving part, the ledger rows of the test route's app, the counter
and production's refusal, and none of them is written by a conversion.

It takes no time from PostgreSQL's own functions and none from the host, and it never moves the clock.
"""

from hashlib import sha256
from importlib import import_module
import re

from django.db import connection
from django.db.migrations import Migration, RunPython
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase, override_settings

from core import clock_startup as startup
from intevia.test_postgresql_backend import clock as owned_route
from tests.test_intevia_clock import REFUSAL_PARTS
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
# CLK-08's two stops on REV-01's descent: the clock's own migration, the moving part's, and the marks production's
# refusal must not name once the production faces are back.
PRODUCTION = import_module("core.migrations.0096_intevia_clock")
CLOCK = ("core", "0096_intevia_clock")
MOVING_PART = (startup.TEST_CLOCK_APP, "0001_moving_part")
OTHER_MARKS = ("the test route's function", "the test route's procedure", "the migration ledger records", "the face public.")


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


def descend(new_executor, graph, after_base, exempt, after_step=None):
    """Unwind the migrations after the base through the executor, a step at a time in its own order.

    Returns what was undone, what could not be, the exemptions that were not needed, and the exemption the descent
    stopped at. Each is taken from the executor's own reports of the migration it began and finished reversing.
    ``after_step``, where given, is called after each step that did not fail, with what has been undone so far."""
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
        if after_step is not None:
            after_step(tuple(undone))
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


def clock_observation():
    """What CLK-08 looks at, at one point of the descent: each face as installed (None where there is none), the
    moving part's routines by name, the migration ledger's rows, the clock's counter, and production's refusal in
    its own words (None where production's startup did not refuse). It asserts nothing, so that no claim stops the
    descent part-way: each is judged once the clone is restored."""
    with connection.cursor() as cursor:
        faces = {name: PRODUCTION.installed(cursor, name) for name, *_rest in PRODUCTION.FACES}
        cursor.execute(
            """SELECT p.proname::text FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND pg_catalog.starts_with(p.proname::text, %s) ORDER BY 1""",
            [startup.TEST_CLOCK_PREFIX])
        moving_part = [row[0] for row in cursor.fetchall()]
        cursor.execute("SELECT app, name FROM public.django_migrations ORDER BY app, name")
        ledger = cursor.fetchall()
    refusal = None
    with override_settings(**PRODUCTION_SETTINGS):
        try:
            startup.refuse_unless_production_clock("default")
        except startup.ClockStartupRefused as refused:
            refusal = str(refused)
    return {"faces": faces, "moving_part": moving_part, "ledger": ledger,
            "counter": instrument.clock_counter_observation(), "refusal": refusal}


def care_edge(care):
    """Observe the two complete catalogue definitions and installed trigger inventory at this stop."""
    with connection.cursor() as cursor:
        digests = []
        for signature in (care.WRITER, care.GATEWAY):
            definition = care.installed_definition(cursor, signature)
            digests.append(sha256((definition or "").encode("utf-8")).hexdigest())
        cursor.execute("""SELECT c.relname, t.tgname, t.tgenabled, t.tgtype, t.tgdeferrable,
                                 t.tginitdeferred, pg_get_triggerdef(t.oid)
                            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                           WHERE c.relnamespace=current_schema()::regnamespace AND NOT t.tgisinternal""")
        triggers = {(row[0], row[1]): tuple(row[2:]) for row in cursor.fetchall()}
        cursor.execute("SELECT app, name FROM django_migrations")
        applied = set(cursor.fetchall())
    delta = sum(n for migration, n in instrument.NON_CORE_TRIGGER_DELTAS.items() if migration in applied)
    return tuple(digests), triggers, delta


# Independently pinned pg_get_functiondef outputs in writer, gateway, matcher, input order.
CARE_ROUTINES_0112 = (
    "39c40cfdb3def4fefba837f49c9cc9685031a907face3c11d28749ba3a7dff4f",
    "496afd9498b1d672636772f954e89d156bc43fda990890c8b6109dc5f5169a15",
    "143854059979571826356840aace0cdd3bf2582db1b80baff07fac53bd4b3f58",
    "e4adcf86cad1c5829fffed3783cfa3eaad250244e0a06aaae2bf29978aebd110",
)
CARE_ROUTINES_0113 = (
    "9993ec0bff9ddd87dbd5330ce150af274154c6259a6fee959b8fd5f733231273",
    "c4be26c5eb58f66008b5a804f24ea4c9bf0f18197864dcdb512445c5ba3fdba1",
    "26cb18bcfb30637f0188a0a3ed25a74b4e308ff6eabbfc93f5d39f030f7c34dd",
    "747d38a216bc9896e82b6b720a323e07402055c754919da77d2a3e237d428c43",
)
CARE_MEMBER_BODY = "d110664005e8c1bdfce7878a7d67c476371189bd15284e4783c4ecc9ee176179"
CARE_MEMBER_KEY = ("core_organismmembership", "care_membership_subject")
CARE_MEMBER_TRIGGER = (
    "O", 7, False, False,
    "CREATE TRIGGER care_membership_subject BEFORE INSERT ON public.core_organismmembership "
    "FOR EACH ROW EXECUTE FUNCTION care_membership_subject()",
)


def account_edge(care, inventory):
    """Read the 0113/0112 evidence using the stable public ID and the real catalogue."""
    with connection.cursor() as cursor:
        extra = []
        for signature in ("public.care_observation_matches(public.core_livingorganismevent)",
                          "public.care_observation_input()"):
            body = care.installed_definition(cursor, signature)
            extra.append(sha256(body.encode()).hexdigest() if body is not None else None)
        cursor.execute("""SELECT u.username, i.canonical_username, i.display_name,
                                 r.canonical_username, r.display_name
                            FROM public.core_identity i
                            JOIN public.auth_user u ON u.id=i.credential_id
                            JOIN public.core_identityresolution r ON r.identity_id=i.id AND r.credential_link=u.id
                           WHERE i.identity_id=%s""", ["b79c7d99-67b3-4dd0-8ab7-ee1328d1e183"])
        triplet = cursor.fetchall()
        cursor.execute("""SELECT (SELECT count(*) FROM public.auth_user),
                                 (SELECT count(*) FROM public.core_identity),
                                 (SELECT count(*) FROM public.core_identityresolution),
                                 (SELECT count(*) FROM public.core_identityresolutionsevered)""")
        counts = cursor.fetchone()
        cursor.execute("""SELECT prosrc FROM pg_proc
                           WHERE oid=to_regprocedure('public.care_membership_subject()')""")
        member_body = cursor.fetchone()
    member_pin = sha256(member_body[0].encode()).hexdigest() if member_body else None
    return inventory[0] + tuple(extra), triplet, counts, member_pin


def colour_mode_present(reader):
    with reader.cursor() as cursor:
        cursor.execute("""SELECT EXISTS (
            SELECT FROM pg_catalog.pg_attribute
             WHERE attrelid = 'public.core_identity'::regclass AND attname = 'colour_mode'
               AND attnum > 0 AND NOT attisdropped)""")
        return cursor.fetchone()[0]


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
        """REV-01, with CLK-08 at two stops of its descent. REV-01: every migration after the declared base (BASE)
        can be unapplied and applied again through Django's migration executor on an owned clone, which returns to
        the state it had, at offset zero, and which production's startup refuses at head, at the lowest point and
        when restored. CLK-08: reversed below the clock's migration and restored, the clone returns to exactly the
        state it had, at offset zero, and production's startup refuses it at head, at each of the two stops and
        when restored. At 0112 the personal colour column must exist, disappear at its
        own inverse and return on reapply. The 0111 CARE protections must survive that edge.
        AC11 observes the account, severance recorder and CARE catalogue on the 0113 round trip."""
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

        # CLK-08 at head, before anything is unwound.
        clock_at_head = clock_observation()
        self.assertIn(MOVING_PART, clock_at_head["ledger"], "CLK-08: this clone must begin with the moving part applied.")
        self.assertIn(CLOCK, clock_at_head["ledger"], "CLK-08: this clone must begin with the clock's migration applied.")
        self.assertEqual(instrument.clock_counter_violations(counter_before, True), [],
                         "CLK-08: the clone must begin with its counter present, at offset zero.")
        self.assertEqual(len(clock_at_head["moving_part"]), 9, "CLK-08: the clone must begin with the nine routines of the moving part.")
        self.assertIn(COUNTER_FOUND, clock_at_head["refusal"] or "",
                      "CLK-08 at head: production's startup must refuse this database, and the refusal must name the counter.")
        clock_stops = {}
        care = import_module("core.migrations.0111_care_office_record")
        care_key = ("core", "0111_care_office_record")
        office_link_key = ("core", "0110_discussion_office_link")
        appearance_key = ("core", "0112_identity_colour_mode")
        account_key = ("core", "0113_care_system_account")
        account_parent = []  # Exact 0112 state immediately after reversing 0113.
        appearance_parent = []
        schema_reader = connection.copy()
        self.addCleanup(schema_reader.close)
        if appearance_key in after_base:
            self.assertTrue(colour_mode_present(schema_reader), "The personal colour preference must exist before reversing 0112.")
        care_parent = []
        care_before = care_edge(care) if care_key in ledger_before else None
        if ("core", "0116_clarity_full_participation") in received:
            _, triggers, non_core = care_before
            self.assertEqual(len(triggers) - non_core, 739, "0116 adds one core trigger and no seed rows.")
            self.assertEqual(triggers[("core_cqact", "cq_br2_confirm")][:4], ("O", 5, False, False),
                             "The BR2 follow-up must be an enabled after-insert row trigger at head.")
        linked_names = [("care", "care", "CARE", "care", "CARE")]
        if account_key in received:
            pins, triplet, counts, member_pin = account_edge(care, care_before)
            self.assertEqual(pins, CARE_ROUTINES_0113, "The installed 0113 must have all four exact CARE routines.")
            self.assertEqual(member_pin, CARE_MEMBER_BODY, "The installed membership refusal must have its pinned body.")
            self.assertEqual(care_before[1].get(CARE_MEMBER_KEY), CARE_MEMBER_TRIGGER,
                             "The installed membership guard must have its exact enabled insert definition.")
            self.assertEqual((triplet, counts), (linked_names, (1, 1, 1, 0)),
                             "The empty 0113 head must have only the linked CARE triplet and no severance.")
        care_triggers = {}
        for kind, table, action in (
            ("setting", "core_careassistancesettingact", "SET_CARE_ASSISTANCE"),
            ("observation", "core_careofficeobservation", "RECORD_CARE_OFFICE_OBSERVATION"),
        ):
            prefix = "care_" + kind
            # pg_trigger bits encode ROW=1, BEFORE=2, INSERT=4, DELETE=8, UPDATE=16, TRUNCATE=32.
            care_triggers.update({
                (table, prefix + "_input"): (7, False, None),
                (table, prefix + "_immutable"): (27, False, None),
                (table, "s015_" + prefix + "_no_truncate"): (34, False, None),
                (table, prefix + "_detail_complete"): (5, True, None),
                ("core_livingorganismevent", prefix + "_event_complete"): (5, True, action),
            })

        def check_care_inventory(snapshot):
            _, triggers, delta = snapshot
            self.assertEqual(len(triggers) - delta, 737,
                             "The 0111 CARE baseline must have 737 triggers before the non-core contribution.")
            for key, (bits, deferred, action) in care_triggers.items():
                self.assertIn(key, triggers, "The 0111 inventory must include the guard " + ".".join(key))
                enabled, actual_bits, deferrable, initially_deferred, ddl = triggers[key]
                self.assertEqual((enabled, actual_bits, deferrable, initially_deferred),
                                 ("O", bits, deferred, deferred),
                                 "The CARE guard must have its specified timing and deferral: " + ".".join(key))
                if action:
                    predicate = ddl.partition(" WHEN (")[2].partition(" EXECUTE ")[0].lower()
                    predicate = re.sub(r"::(?:character varying|text)|[()\s]", "", predicate)
                    self.assertEqual(predicate, "new.action='" + action.lower() + "'",
                                     "The ILC trigger must fire only on its own CARE action.")
                else:
                    self.assertNotIn(" WHEN (", ddl, "A CARE detail guard must cover every row at its specified operation.")

        # Preserve the head-time guard witness for these two known, trigger-equivalent heads.
        # A later core head may add guards; the 737 baseline then belongs to the 0111 stop.
        check_inventory_at_head = care_key in received or appearance_key in received
        if check_inventory_at_head:
            check_care_inventory(care_before)

        office_link = import_module("core.migrations.0110_discussion_office_link")
        office_parent = []

        def office_bodies():
            with connection.cursor() as cursor:
                cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(%s)", [office_link.EXECUTE])
                writer = cursor.fetchone()[0]
                cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(%s)", [office_link.INPUT])
                return writer, cursor.fetchone()[0]

        chair = import_module("core.migrations.0109_committee_chair_reservation")
        chair_parent = []
        reseat = import_module("core.migrations.0108_circle_reseat")
        reseat_parent = []
        committee_parent = []
        committee_cut = import_module("core.migrations.0107_committee_circle_dormancy")
        circle_parent = []  # Observe the 0106 -> 0105 edge on the same descent.
        records = import_module("core.migrations.0106_circle_vacancy_records")

        def observe_clock(undone_so_far):
            if not account_parent and account_key in undone_so_far and appearance_key not in undone_so_far:
                inventory = care_edge(care)
                account_parent.append((inventory, account_edge(care, inventory)))
            if not appearance_parent and appearance_key in undone_so_far and care_key not in undone_so_far:
                appearance_parent.append((colour_mode_present(schema_reader), care_edge(care)))
            if not care_parent and care_key in undone_so_far and office_link_key not in undone_so_far:
                care_parent.append(care_edge(care))
            if (not office_parent and ("core", "0110_discussion_office_link") in undone_so_far
                    and ("core", "0109_committee_chair_reservation") not in undone_so_far):
                office_parent.append(office_bodies())
            if (not chair_parent and ("core", "0109_committee_chair_reservation") in undone_so_far
                    and ("core", "0108_circle_reseat") not in undone_so_far):
                with connection.cursor() as cursor:
                    chair.expect(cursor, False)
                    reseat.expect(cursor, True)
                chair_parent.append(True)
            if (not reseat_parent and ("core", "0108_circle_reseat") in undone_so_far
                    and ("core", "0107_committee_circle_dormancy") not in undone_so_far):
                with connection.cursor() as cursor:
                    reseat_parent.append((reseat.definition(cursor, reseat.RESEATABLE),
                                          reseat.definition(cursor, committee_cut.APART)))
            if (not committee_parent and ("core", "0107_committee_circle_dormancy") in undone_so_far
                    and ("core", "0106_circle_vacancy_records") not in undone_so_far):
                with connection.cursor() as cursor:
                    committee_parent.append((records.episode.definition(cursor, committee_cut.APART),
                                             records.episode.definition(cursor, committee_cut.REPLACEMENTS[0][0])))
            if (not circle_parent and ("core", "0106_circle_vacancy_records") in undone_so_far
                    and ("core", "0105_circle_vacancy_episode") not in undone_so_far):
                with connection.cursor() as cursor:
                    circle_parent.append((records.episode.definition(cursor, records.WRITER),
                                          records.trigger(cursor, "zz_circle_qualified_activation")))
            # CLK-08's first stop: the moving part reversed, the clock's own migration still applied.
            if "reversed" not in clock_stops and MOVING_PART in undone_so_far and CLOCK not in undone_so_far:
                clock_stops["reversed"] = clock_observation()

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
        undone, not_undone, needless, stopped_at = descend(real_executor, graph, after_base, exempt, observe_clock)
        left = [key for key in after_base if key not in undone]
        at_the_base = (ledger(), instrument.clock_counter_observation(), production_startup("at the lowest point")) if not not_undone else None
        if not not_undone and MOVING_PART in undone and CLOCK in undone:
            clock_stops["below"] = clock_observation()   # CLK-08's second stop: below the clock, no face at all
        failed_again = restore_failure(not_undone, restore())

        # Judge the inverse before reporting any dependent reapply failure.
        if account_key in undone:
            self.assertEqual(len(account_parent), 1, "The descent must observe the 0113 inverse at 0112.")
            parent_inventory, (pins, triplet, counts, member_pin) = account_parent[0]
            self.assertEqual(pins, CARE_ROUTINES_0112, "The empty inverse must restore all four exact 0112 CARE routines.")
            self.assertNotIn(CARE_MEMBER_KEY, parent_inventory[1], "The 0113 membership trigger must be absent at 0112.")
            self.assertIsNone(member_pin, "The membership refusal routine must be absent at 0112.")
            recorder = parent_inventory[1].get(("core_identityresolution", "s015_0022_resolution_severance"))
            self.assertEqual(recorder and recorder[0], "O", "The inverse must leave the severance recorder enabled at 0112.")
            self.assertEqual((triplet, counts), ([], (0, 0, 0, 0)),
                             "The empty inverse must remove all three CARE records without leaving a severance.")
        if appearance_key in undone:
            self.assertEqual(len(appearance_parent), 1, "The descent must observe 0112 reversed while 0111 is installed.")
            field_present, parent_care = appearance_parent[0]
            self.assertFalse(field_present, "Reversing 0112 must remove the personal colour preference.")
            if account_parent or appearance_key in received:
                at_0112 = account_parent[0][0] if account_parent else care_before
                self.assertEqual(parent_care, at_0112,
                                 "Reversing 0112 must preserve both CARE functions and the full trigger inventory.")

        # CLK-08 at its two stops, judged before REV-01's own verdict so that each claim reports its own failure.
        with self.subTest(claim="CLK-08", point="the two stops"):
            self.assertEqual(sorted(clock_stops), ["below", "reversed"],
                             "CLK-08: the descent must pass both of CLK-08's stops: the moving part reversed with the clock's own "
                             "migration still applied, and then both reversed. A descent that fails, or that an exemption stops "
                             "above the clock's migration, leaves CLK-08 without its witness.")
            written = {name: PRODUCTION.expected(stored, builtin, meaning) for name, _w, stored, builtin, meaning in PRODUCTION.FACES}
            stop = clock_stops["reversed"]
            self.assertEqual(stop["faces"], written, "CLK-08 reversed: the faces must be the production faces, exactly as 0096 writes them.")
            self.assertEqual(stop["moving_part"], [], "CLK-08 reversed: no function of the moving part may remain.")
            self.assertNotIn(startup.TEST_CLOCK_APP, [app for app, _name in stop["ledger"]],
                             "CLK-08 reversed: the ledger must hold no row for the test route's app.")
            self.assertIn(CLOCK, stop["ledger"], "CLK-08 reversed: the clock's own migration must still be applied at this stop.")
            self.assertEqual(stop["counter"], counter_before, "CLK-08 reversed: the counter must be kept, at zero and exactly as it was.")
            self.assertIn(COUNTER_FOUND, stop["refusal"] or "",
                          "CLK-08 reversed: production's startup must refuse this database, and the refusal must name the counter.")
            self.assertEqual([mark for mark in OTHER_MARKS if mark in stop["refusal"]], [],
                             "CLK-08 reversed: with the production faces back, the counter must be the only mark found.")
            stop = clock_stops["below"]
            self.assertEqual(stop["faces"], {name: None for name in written}, "CLK-08 below the clock: no face may remain.")
            self.assertEqual(stop["moving_part"], [], "CLK-08 below the clock: no function of the moving part may remain.")
            self.assertEqual([row for row in stop["ledger"] if row in (MOVING_PART, CLOCK)], [],
                             "CLK-08 below the clock: the ledger must hold neither migration.")
            self.assertEqual(stop["counter"], counter_before, "CLK-08 below the clock: the counter must be kept, at zero and exactly as it was.")
            self.assertIn(COUNTER_FOUND, stop["refusal"] or "",
                          "CLK-08 below the clock: production's startup must refuse this database, and the refusal must name the counter.")
            for part in REFUSAL_PARTS:
                self.assertIn(part, stop["refusal"], "CLK-08 below the clock: the refusal must keep its five parts.")
        if failed_again:
            self.fail(failed_again)
        if appearance_key in after_base:
            self.assertTrue(colour_mode_present(schema_reader), "Reapplying 0112 must restore the personal colour preference.")
        if account_key in received:
            restored_inventory = care_edge(care)
            pins, triplet, counts, member_pin = account_edge(care, restored_inventory)
            self.assertEqual(pins, CARE_ROUTINES_0113, "Reapplying 0113 must restore the four pinned CARE routines.")
            self.assertEqual(member_pin, CARE_MEMBER_BODY, "Reapplying 0113 must restore the membership refusal body.")
            self.assertEqual(restored_inventory[1].get(CARE_MEMBER_KEY), CARE_MEMBER_TRIGGER,
                             "Reapplying 0113 must restore the exact enabled membership trigger.")
            self.assertEqual((triplet, counts), (linked_names, (1, 1, 1, 0)),
                             "Reapplying 0113 must restore the linked triplet under CARE's original UUID.")
        if care_key in after_base:
            self.assertEqual(len(care_parent), 1, "REV-01 must observe the empty 0111 to 0110 edge once.")
            digests, parent_triggers, delta = care_parent[0]
            self.assertEqual(digests, (
                "1a5a28da471b96c04e1cbc36f936d2a1188f63d6b1e940ec8e8d24d656b0ae63",
                "ac69095df8c59d0d9dadfdede5f49b409fd1af8007e78d3db9667ad811786ca7"),
                "The empty inverse must restore both complete captured 0110 routine definitions.")
            self.assertEqual(len(parent_triggers) - delta, 727,
                             "The empty 0110 parent must have 727 triggers before its non-core contribution.")
            self.assertTrue(care_triggers.keys().isdisjoint(parent_triggers),
                            "The empty inverse must remove all ten CARE triggers.")
            care_at_0111 = appearance_parent[0][1] if appearance_key in undone else care_before
            if not check_inventory_at_head:
                check_care_inventory(care_at_0111)
            triggers = care_at_0111[1]
            prior_inventory = {key: row for key, row in triggers.items() if key not in care_triggers}
            self.assertEqual(parent_triggers, prior_inventory,
                             "The empty inverse must preserve every earlier trigger definition and enabled state.")
            self.assertEqual(care_edge(care), care_before,
                             "Restoring the received head must restore both CARE definitions and the complete trigger inventory.")
            self.assertEqual(care_at_0111[0], (
                "39c40cfdb3def4fefba837f49c9cc9685031a907face3c11d28749ba3a7dff4f",
                "496afd9498b1d672636772f954e89d156bc43fda990890c8b6109dc5f5169a15"),
                "The 0111 boundary must have both independently pinned CARE definitions.")
        self.assertEqual(office_parent, [tuple(prior for _, prior, _ in office_link.REPLACEMENTS)],
                         "Downgrading 0110 must restore both clock-converted posting functions exactly.")
        self.assertEqual(office_bodies(), tuple(after for _, _, after in office_link.REPLACEMENTS),
                         "Reapplying 0110 must restore both guarded posting functions exactly.")
        self.assertEqual(reseat_parent, [(None, committee_cut.APART_SQL)])
        self.assertEqual(committee_parent, [(None, committee_cut.LIVE_OLD)],
                         "REV-01 must observe the landed mandate reader at the 0106 parent.")
        self.assertEqual(circle_parent, [(None, records.GUARD_TRIGGER)],
                         "REV-01 must see the 0105 trigger and no 0106 writer at the parent.")
        with connection.cursor() as cursor:
            self.assertEqual(chair_parent, [True], "REV-01 must see the exact 0108 bodies before continuing downward.")
            chair.expect(cursor, True)
            for signature, _, after in reseat.REPLACEMENTS:
                installed = reseat.definition(cursor, signature)
                if signature == care.WRITER and ("core", "0111_care_office_record") in ledger_before:
                    # 0113 replaces this writer. The old digest is still required at the 0111 and 0112 stops.
                    self.assertEqual(
                        sha256((installed or "").encode("utf-8")).hexdigest(),
                        CARE_ROUTINES_0113[0] if account_key in ledger_before else CARE_ROUTINES_0112[0],
                        "Restoration must recover the exact CARE event writer for the received schema.",
                    )
                else:
                    self.assertEqual(installed, after, "Restoration must preserve this 0108 function: " + signature)
            for signature, body in reseat.ADDITIONS:
                if signature != reseat.RESEATABLE:
                    self.assertEqual(reseat.definition(cursor, signature), body, "Restoring 0109 must preserve this 0108 addition: " + signature)
            self.assertEqual(records.episode.definition(cursor, records.WRITER), records.WRITE_SQL,
                             "REV-01 must restore the 0106 writer at head.")
        with self.subTest(claim="CLK-08", point="restored"):
            clock_restored = clock_observation()
            restored_capture = instrument.capture()
            self.assertEqual(instrument.compare(before, restored_capture), ([], []), "CLK-08 restored: the full capture must equal the one taken at head.")
            self.assertEqual(restored_capture["intevia_clock_counter"], counter_before, "CLK-08 restored: the counter must be the one kept, at zero.")
            self.assertEqual(clock_restored["faces"], clock_at_head["faces"], "CLK-08 restored: the faces and their comments must be as they were.")
            self.assertEqual(clock_restored["moving_part"], clock_at_head["moving_part"], "CLK-08 restored: the nine routines must be back.")
            self.assertEqual(clock_restored["ledger"], clock_at_head["ledger"], "CLK-08 restored: the ledger must be as it was received.")
            self.assertEqual(owned_route.offset(), 0, "CLK-08 restored: the clock must be at zero.")
            self.assertIn(COUNTER_FOUND, clock_restored["refusal"] or "", "CLK-08 restored: production's startup must still refuse.")


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
