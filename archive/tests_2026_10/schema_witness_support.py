"""Schema witnesses without frozen whole-system values (US015-B-35, brittle-test repair).

The Human Governor's direction of 27 Oct 2026: an ordinary test never compares the live schema with a frozen total
(all triggers, all foreign keys, all tables, a catalogue's size) or with a hard-coded "current" migration name. Such a
value fails whenever unrelated, legitimate work is added, and proves nothing about the safeguard the test is about.

What a test asserts instead, with the helpers here:
- the named object it is about exists, is enabled and is bound to the right table and function (trigger_detail);
- what a migration adds or removes, as a delta of named objects between two states in the same test
  (assert_trigger_delta);
- that a round trip or a rolled-back block restores exactly the set it started from (trigger_names before and after);
- that the database is at the latest migration of the code under test, read from the migration graph, not a literal
  (assert_at_code_head).
The one version-bound whole-schema contract (REQUIRED_TRIGGER_COUNT and the seed-row map) stays in the dedicated
verification machinery, verification/isolation/state.py, and is not copied into tests.
"""

from django.db import connection


def trigger_names(using=None):
    """Every user (non-internal) trigger in the public schema, as a frozenset of (table, trigger)."""
    conn = using or connection
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname, t.tgname FROM pg_catalog.pg_trigger t "
            "JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
        )
        return frozenset(cursor.fetchall())


def trigger_detail(table, trigger, using=None):
    """(enabled, deferrable, initially_deferred, function) of one named trigger on one table, or None."""
    conn = using or connection
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT t.tgenabled, t.tgdeferrable, t.tginitdeferred, p.proname "
            "FROM pg_catalog.pg_trigger t "
            "JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid "
            "WHERE n.nspname = 'public' AND NOT t.tgisinternal AND c.relname = %s AND t.tgname = %s",
            [table, trigger],
        )
        rows = cursor.fetchall()
    if len(rows) > 1:
        raise AssertionError(f"trigger {trigger} appears {len(rows)} times on {table}")
    return rows[0] if rows else None


def assert_trigger_bound(test_case, table, trigger, function, *, deferrable=None, initially_deferred=None):
    """The named trigger exists on the table, is enabled ('O') and calls the named function."""
    detail = trigger_detail(table, trigger)
    test_case.assertIsNotNone(detail, f"trigger {trigger} is missing on {table}")
    enabled, is_deferrable, is_deferred, proname = detail
    test_case.assertEqual(enabled, "O", f"trigger {trigger} on {table} is not enabled")
    test_case.assertEqual(proname, function, f"trigger {trigger} on {table} calls {proname}, not {function}")
    if deferrable is not None:
        test_case.assertEqual(is_deferrable, deferrable, f"trigger {trigger} deferrable={is_deferrable}")
    if initially_deferred is not None:
        test_case.assertEqual(is_deferred, initially_deferred, f"trigger {trigger} initially deferred={is_deferred}")


def assert_trigger_delta(test_case, before, after, *, added=(), removed=()):
    """Between two states in the same test, exactly the named triggers were added and removed; nothing else changed."""
    test_case.assertEqual(set(after - before), set(added), "triggers added differ from the declared set")
    test_case.assertEqual(set(before - after), set(removed), "triggers removed differ from the declared set")


def code_core_head(using=None):
    """The latest core migration of the code under test, from the migration graph (not from the database)."""
    from django.db.migrations.executor import MigrationExecutor

    leaves = MigrationExecutor(using or connection).loader.graph.leaf_nodes("core")
    if len(leaves) != 1:
        raise AssertionError(f"expected one core migration leaf, found {leaves!r}")
    return leaves[0]


def applied_core_head(using=None):
    """The one applied core migration head, read from the ledger against the graph."""
    from tests.s015_0021_support import current_applied_core_head

    return current_applied_core_head(using or connection)[0]


def assert_at_code_head(test_case, using=None):
    """The database has every core migration of the code under test applied, and no other head."""
    test_case.assertEqual(applied_core_head(using), code_core_head(using))


def _identity_fk_pairs(apps):
    pairs = set()
    for model in apps.get_app_config("core").get_models():
        for field in model._meta.get_fields():
            related = getattr(field, "related_model", None)
            if related is None or isinstance(related, str) or not hasattr(field, "column"):
                continue
            if related._meta.label_lower == "core.identity":
                pairs.add((model._meta.model_name, field.name))
    return pairs


def identity_fks_at(target):
    """(model, field) pairs referencing Identity in the historical model state at a core migration.

    Read from the code's migration history (independent of any database): history fixes these sets, so a
    witness can compare a slice's declared Identity FKs with what its migration actually added.
    """
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    return _identity_fk_pairs(loader.project_state(target).apps)


def live_identity_fks():
    """(model, field) pairs referencing Identity in the code's current models."""
    from django.apps import apps

    return _identity_fk_pairs(apps)
