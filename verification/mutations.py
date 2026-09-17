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

MUTATION_ENV = "VERIFICATION_MUTATION"

MUTATIONS = {
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


class MutationRunner(OwnedDatabaseRunnerMixin, DiscoverRunner):
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
