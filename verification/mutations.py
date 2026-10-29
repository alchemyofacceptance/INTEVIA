"""Discrimination checks for the S015 verification route.

A negative test is only as good as the refusal it demands. Each mutation below replaces one intended protection with
an unrelated refusal that blocks the same operation. Under the mutation, a test that demands the intended refusal must
FAIL; a test that accepts any refusal would still pass. The route runs each mutation's target tests with
MutationRunner and requires every target to fail by assertion.

A mutation is applied only inside the attempt-owned disposable test database, after the test runner has created and
migrated it, and is destroyed with it.
"""
import os

from django.db import connection
from django.test.runner import DiscoverRunner

from verification.ownership import OwnedDatabaseRunnerMixin
from verification.recording import RecordingRunnerMixin

MUTATION_ENV = "VERIFICATION_MUTATION"

MUTATIONS = {
    "o24-terminal-refusal": {
        # RECURRING: the terminal-conflict test must discriminate the named guardian refusal.
        "follow_up": "O-24 TAR-3 recurring experiment",
        "intended": "S3 terminal exclusion on a conflicting decision/withdrawal write",
        "targets": [
            "test_seed_o24_application_terminal_finality.ApplicationTerminalFinalityTests."
            "test_decision_then_withdrawal_refuses_at_earlier_and_later_effective_times"
        ],
        "sql": [
            """DO $$
               DECLARE definition text;
               BEGIN
                   SELECT pg_get_functiondef('public.s3_terminal_lock()'::regprocedure)
                     INTO definition;
                   IF definition IS NULL OR position('S3 terminal exclusion' IN definition) = 0 THEN
                       RAISE EXCEPTION 'expected terminal-exclusion refusal was not found';
                   END IF;
                   EXECUTE replace(
                       definition,
                       'S3 terminal exclusion',
                       'VERIFICATION MUTATION: an unrelated refusal'
                   );
               END
               $$""",
        ],
    },
    "o24-engagement-refusal-admission": {
        # ADMISSION: after-withdrawal engagement must demand the named engagement guardian.
        "follow_up": "O-24 TAR-3 engagement admission evidence",
        "intended": "S3 engagement refused when an engagement is attempted after withdrawal",
        "targets": [
            "test_seed_o24_application_terminal_finality.ApplicationTerminalFinalityTests."
            "test_withdrawal_then_decision_and_engagement_refuse_independently"
        ],
        "sql": [
            """DO $$
               DECLARE definition text;
               BEGIN
                   SELECT pg_get_functiondef('public.s3_engagement_guard()'::regprocedure)
                     INTO definition;
                   IF definition IS NULL OR position('S3 engagement refused' IN definition) = 0 THEN
                       RAISE EXCEPTION 'expected engagement refusal was not found';
                   END IF;
                   EXECUTE replace(
                       definition,
                       'S3 engagement refused',
                       'VERIFICATION MUTATION: an unrelated refusal'
                   );
               END
               $$""",
        ],
    },
    "o24-decision-uniqueness-admission": {
        # ADMISSION: two decisions must be excluded by the one-to-one application key.
        "follow_up": "O-24 TAR-3 same-kind admission evidence",
        "intended": "the unique application key on core_applicationdecision.application_id",
        "targets": [
            "test_seed_o24_application_terminal_finality.ApplicationTerminalFinalityTests."
            "test_second_decision_is_refused_by_application_uniqueness"
        ],
        "sql": [
            """DO $$
               DECLARE
                   application_att smallint;
                   unique_constraint text;
                   unique_index oid;
               BEGIN
                   SELECT attnum INTO application_att
                     FROM pg_catalog.pg_attribute
                    WHERE attrelid = 'public.core_applicationdecision'::regclass
                      AND attname = 'application_id'
                      AND NOT attisdropped;
                   SELECT conname INTO unique_constraint
                     FROM pg_catalog.pg_constraint
                    WHERE conrelid = 'public.core_applicationdecision'::regclass
                      AND contype = 'u'
                      AND cardinality(conkey) = 1
                      AND conkey[1] = application_att;
                   IF unique_constraint IS NOT NULL THEN
                       EXECUTE format(
                           'ALTER TABLE public.core_applicationdecision DROP CONSTRAINT %I',
                           unique_constraint
                       );
                   ELSE
                       SELECT indexrelid INTO unique_index
                         FROM pg_catalog.pg_index
                        WHERE indrelid = 'public.core_applicationdecision'::regclass
                          AND indisunique
                          AND indnatts = 1
                          AND pg_get_indexdef(indexrelid, 1, true) = 'application_id';
                       IF unique_index IS NULL THEN
                           RAISE EXCEPTION 'application decision unique key was not found';
                       END IF;
                       EXECUTE format('DROP INDEX %s', unique_index::regclass);
                   END IF;
               END
               $$""",
        ],
    },
    "o24-terminal-race-admission": {
        # ADMISSION: sensitivity to removal of terminal exclusion under contention.
        # Decision first fails the loser's refusal-text assertion; withdrawal first fails
        # the one-commit assertion. This does not guarantee a two-row witness.
        "follow_up": "O-24 TAR-3 threaded admission evidence",
        "intended": (
            "sensitivity to removal of terminal exclusion under contention; "
            "decision first fails the loser's refusal-text assertion, while "
            "withdrawal first fails the one-commit assertion; it does not "
            "guarantee a two-row witness"
        ),
        "targets": [
            "test_seed_o24_application_terminal_finality.ApplicationTerminalFinalityTests."
            "test_threaded_decision_and_withdrawal_leave_one_terminal_row"
        ],
        "sql": [
            """CREATE OR REPLACE FUNCTION public.s3_terminal_lock()
               RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN
                   PERFORM public.s3_lock_application(NEW.application_id);
                   NEW.recorded_at := clock_timestamp();
                   RETURN NEW;
               END
               $$""",
        ],
    },
    "fu-b2-unrelated-reuse-refusal": {
        "follow_up": "FU-B2 (A1 review O-2)",
        "intended": "the reuse guardian's R-d refusal: R-d as ruled and landed (ILC Datacron section 3, What the packet builds, item 2; section 6) and implemented by 0022 RESOLUTION_REUSE_GUARD_BODY; design v0.6 section 5.5 posed the question",
        "targets": ["test_s015_0022_contract.S0150022ContractTests.test_reinsert_after_severance_is_refused"],
        "sql": [
            """CREATE OR REPLACE FUNCTION public.s015_0022_guard_resolution_reuse()
               RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
               BEGIN
                   IF EXISTS (SELECT 1 FROM public.core_identityresolutionsevered s WHERE s.identity_id = NEW.identity_id) THEN
                       RAISE EXCEPTION 'VERIFICATION MUTATION: an unrelated refusal' USING ERRCODE = 'integrity_constraint_violation';
                   END IF;
                   RETURN NEW;
               END;
               $$""",
        ],
    },
    "fu-b3-unrelated-circle-check": {
        "follow_up": "FU-B3 (A1 review O-2)",
        "intended": "the circle event resulting-state vocabulary CHECK s015_0021_circleevent_resulting_state_vocab_ck (0021)",
        "targets": ["test_s015_0021_negative_direct_sql.VocabulariesAndShapes.test_neg_circle_active_on_event"],
        "sql": [
            "ALTER TABLE public.core_circlestateevent DROP CONSTRAINT s015_0021_circleevent_resulting_state_vocab_ck",
            "ALTER TABLE public.core_circlestateevent ADD CONSTRAINT verification_mutation_unrelated_ck CHECK (resulting_state <> 'ACTIVE')",
        ],
    },
}


class MutationRunner(RecordingRunnerMixin, OwnedDatabaseRunnerMixin, DiscoverRunner):
    """Applies the mutation named in VERIFICATION_MUTATION to the freshly migrated test database, before any test."""

    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        name = os.environ.get(MUTATION_ENV)
        if name not in MUTATIONS:
            raise RuntimeError("unknown or missing %s: %r" % (MUTATION_ENV, name))
        with connection.cursor() as cursor:
            for statement in MUTATIONS[name]["sql"]:
                cursor.execute(statement)
        print("VERIFICATION MUTATION APPLIED: %s" % name, flush=True)
        return old_config
