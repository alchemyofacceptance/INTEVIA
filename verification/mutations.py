"""Discrimination checks for the S015 verification route.

A test must distinguish the intended behaviour from a named defect. These mutations substitute an unrelated
refusal, remove a protection, or introduce an overbroad refusal. The route runs each mutation's target tests with
MutationRunner and requires every target to fail by assertion, rather than error during setup or execution.

A mutation is applied only inside the attempt-owned disposable test database, after the test runner has created it,
and is destroyed with it. Since CI-CLONE-1 (Human Governor, 31 Oct 2026: clone mode wherever a database is created) the
runner shares the TT-1 template path with the isolation runner: the test database is a clone of the run's template,
and the mutation SQL is executed on the clone's connection only. The template is sealed against connections
(ALLOW_CONNECTIONS false, witnessed in its receipt), so no statement run here can reach it; and before the mutation is
applied the clone's CP-0 digest is recomputed and must equal the template's recorded fresh-build CP-0: the compared CP-0
properties of the clone match the fresh-build reference at the moment the clone was taken (that is, after every earlier
mutation step). A mismatch does not say whether the template or the clone is at fault; it refuses the step.
"""
import os
import sys
import time

from django.db import connection
from django.test.runner import DiscoverRunner

from verification.isolation.template_mode import TemplateModeRunnerMixin
from verification.ownership import OwnedDatabaseRunnerMixin, write_receipt
from verification.recording import RecordingRunnerMixin

MUTATION_ENV = "VERIFICATION_MUTATION"

MUTATIONS = {
    "cq-settings-result-reference": {
        "follow_up": "DISC4-01 recurring control",
        "intended": "The voted settings version cites itself instead of its qualifying result.",
        "targets": ["test_cq_settings_authority.CQSettingsAuthorityTests.test_01_qualified_settings_cite_deciding_result"],
        "sql": ["""CREATE OR REPLACE FUNCTION public.cq4_decision(p_version uuid,p_source jsonb)
RETURNS uuid LANGUAGE sql IMMUTABLE AS $$ SELECT p_version $$;"""],
    },
    "cq-qualified-effect-only": {
        "follow_up": "DISC3-02 recurring control",
        "intended": "A defeated proposal changes the law.",
        "targets": ["test_cq_authority.CQAuthorityTests.test_defeated_attachment_has_no_effect"],
        "sql": ["""CREATE OR REPLACE FUNCTION public.cq3_qualified(p_kind text)
RETURNS boolean LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT p_kind IN ('QUALIFIED','NOT_QUALIFIED_VOTE')
$$;"""],
    },
    "cq-default-tie-refusal": {
        "follow_up": "DISC2-11 recurring control",
        "intended": "A quorate default tie incorrectly qualifies.",
        "targets": ["test_cq_decisions.CQDecisionTests.test_default_tie_and_exact_threshold_bases"],
        "sql": ["""DO $mutation$
DECLARE definition text; marker text := 'IF p_yes=p_no THEN v_kind:=''NOT_QUALIFIED_TIE''; -- CQ_DEFAULT_TIE';
BEGIN
 SELECT pg_get_functiondef('public.cq_count_decision(jsonb,integer,integer,integer,integer)'::regprocedure) INTO STRICT definition;
 IF length(definition)-length(replace(definition,marker,''))<>length(marker) THEN
   RAISE EXCEPTION 'DISC2 requires exactly one tie-result marker'; END IF;
 EXECUTE replace(definition,marker,'IF p_yes=p_no THEN v_kind:=''QUALIFIED''; -- CQ_DEFAULT_TIE');
END $mutation$"""],
    },
    "disc-free-category-cap": {
        "follow_up": "DISC1-04 recurring control",
        "intended": "A free board accepts its tenth custom category.",
        "targets": ["test_discussion_boards.DiscussionBoardTests.test_free_cap_beta_exception_and_lapse_projection"],
        "sql": ["""DO $mutation$
DECLARE definition text; marker text := 'IF v_count>=9 AND NOT public.s015_entitlement_effective(v_lo,''DISCUSSION_CUSTOM_CATEGORIES'',v_stamp) THEN -- DISC_FREE_CAP';
BEGIN
 SELECT pg_get_functiondef('public.disc_input()'::regprocedure) INTO STRICT definition;
 IF length(definition)-length(replace(definition,marker,''))<>length(marker) THEN
   RAISE EXCEPTION 'DISC1 requires exactly one cap predicate'; END IF;
 EXECUTE replace(definition,marker,'IF false THEN -- DISC_FREE_CAP');
END $mutation$"""],
    },
    "gi-law-in-force-required": {
        "follow_up": "GI-01 recurring control",
        "intended": "A GI is recorded without any qualifying law in force.",
        "targets": ["test_guiding_intentions.GuidingIntentionTests.test_exact_local_law_required_for_lo_and_circle"],
        "sql": ["""DO $mutation$
DECLARE definition text; marker text := 'RETURN v_total>0 AND v_valid=v_total AND v_local>0; -- GI_LOCAL_LAW_REQUIRED';
BEGIN
 SELECT pg_get_functiondef('public.gi_laws_ok(uuid,bigint,jsonb,timestamptz,timestamptz)'::regprocedure)
 INTO STRICT definition;
 IF length(definition)-length(replace(definition,marker,''))<>length(marker) THEN
   RAISE EXCEPTION 'GI requires exactly one local-law predicate';
 END IF;
 EXECUTE replace(definition,marker,'RETURN true; -- GI_LOCAL_LAW_REQUIRED');
END $mutation$"""],
    },
    "lib-wrong-law-text-version": {
        "follow_up": "LIB-01 recurring control",
        "intended": "Our laws leaks the latest text into the historical binding version.",
        "targets": ["test_library_laws.LibraryLawsTests.test_our_laws_exact_versions_at_two_cuts"],
        "sql": ["""DO $mutation$
DECLARE definition text; marker text := 'v.id=l.version';
BEGIN
 SELECT pg_get_functiondef('public.lib_laws_at(uuid,timestamptz,timestamptz)'::regprocedure)
 INTO STRICT definition;
 IF length(definition)-length(replace(definition,marker,''))<>length(marker) THEN
   RAISE EXCEPTION 'LIB requires exactly one binding-version text join';
 END IF;
 EXECUTE replace(definition,marker,'v.id=r.current_version_id');
END $mutation$"""],
    },
    "l-overlapping-law-windows": {
        "follow_up": "L-01 G5 recurring control",
        "intended": "Two LAW versions of the same instrument in the same scope overlap.",
        "targets": ["test_law_records.LawRecordTests.test_overlapping_versions_and_competing_first_windows"],
        # Preserve the constraint's name, type and deferred SET CONSTRAINTS interface.
        # Only its coverage changes. The second insert must survive the named check,
        # so L-01 fails with its intended assertion instead of an undefined-name error.
        "sql": ["""ALTER TABLE public.core_organismlawbinding
                   DROP CONSTRAINT l_one_version_per_scope,
                   ADD CONSTRAINT l_one_version_per_scope EXCLUDE USING gist
                   (numrange(resource_id::numeric,resource_id::numeric,'[]') WITH &&,
                    int4range((CASE scope_kind WHEN 'PLATFORM' THEN 0 WHEN 'CIRCLE' THEN 1 ELSE 2 END),
                              (CASE scope_kind WHEN 'PLATFORM' THEN 0 WHEN 'CIRCLE' THEN 1 ELSE 2 END),'[]') WITH &&,
                    numrange((CASE scope_kind WHEN 'PLATFORM' THEN 0 WHEN 'CIRCLE' THEN scope_circle_id ELSE organism_id END)::numeric,
                             (CASE scope_kind WHEN 'PLATFORM' THEN 0 WHEN 'CIRCLE' THEN scope_circle_id ELSE organism_id END)::numeric,'[]') WITH &&,
                    tstzrange(in_force_from,in_force_until,'[)') WITH &&)
                   WHERE (false) DEFERRABLE INITIALLY DEFERRED"""],
    },
    "r-nomination-without-activation": {
        "follow_up": "R G5 recurring control",
        "intended": "A named Guiding successor receives publication authority from nomination alone.",
        "targets": ["test_root_organism.RootOrganismTests.test_nomination_and_death_succession_authority"],
        "sql": ["""DO $mutation$
DECLARE definition text; marker text := 'IF activation IS NULL THEN RETURN NULL; END IF;';
BEGIN
 SELECT pg_get_functiondef('public.r_publication_capacity(bigint,bigint,text,timestamptz,timestamptz)'::regprocedure)
 INTO STRICT definition;
 IF length(definition)-length(replace(definition,marker,''))<>length(marker) THEN
   RAISE EXCEPTION 'R G5 requires exactly one missing-activation refusal';
 END IF;
 EXECUTE replace(definition,marker,
   'IF activation IS NULL THEN IF person=public.r_successor(lo,''DEATH'',cut,known) THEN RETURN ''NOMINATED''; END IF; RETURN NULL; END IF;');
END $mutation$"""],
    },
    "o24-terminal-refusal": {
        # RECURRING: the terminal-conflict test must discriminate the named guardian refusal.
        "follow_up": "O-24 TAR-3 recurring experiment",
        "intended": "S3 terminal exclusion on a conflicting decision/withdrawal write",
        "targets": [
            "test_seed_o24_application_terminal_finality.ApplicationTerminalFinalityTests.test_decision_then_withdrawal_refuses_at_earlier_and_later_effective_times"
        ],
        "sql": [
            "DO $$\n               DECLARE definition text;\n               BEGIN\n                   SELECT pg_get_functiondef('public.s3_terminal_lock()'::regprocedure)\n                     INTO definition;\n                   IF definition IS NULL OR position('S3 terminal exclusion' IN definition) = 0 THEN\n                       RAISE EXCEPTION 'expected terminal-exclusion refusal was not found';\n                   END IF;\n                   EXECUTE replace(\n                       definition,\n                       'S3 terminal exclusion',\n                       'VERIFICATION MUTATION: an unrelated refusal'\n                   );\n               END\n               $$"
        ]
    },
    "o24-engagement-refusal-admission": {
        # ADMISSION: after-withdrawal engagement must demand the named engagement guardian.
        "follow_up": "O-24 TAR-3 engagement admission evidence",
        "intended": "S3 engagement refused when an engagement is attempted after withdrawal",
        "targets": [
            "test_seed_o24_application_terminal_finality.ApplicationTerminalFinalityTests.test_withdrawal_then_decision_and_engagement_refuse_independently"
        ],
        "sql": [
            "DO $$\n               DECLARE definition text;\n               BEGIN\n                   SELECT pg_get_functiondef('public.s3_engagement_guard()'::regprocedure)\n                     INTO definition;\n                   IF definition IS NULL OR position('S3 engagement refused' IN definition) = 0 THEN\n                       RAISE EXCEPTION 'expected engagement refusal was not found';\n                   END IF;\n                   EXECUTE replace(\n                       definition,\n                       'S3 engagement refused',\n                       'VERIFICATION MUTATION: an unrelated refusal'\n                   );\n               END\n               $$"
        ]
    },
    "o24-decision-uniqueness-admission": {
        # ADMISSION: two decisions must be excluded by the one-to-one application key.
        "follow_up": "O-24 TAR-3 same-kind admission evidence",
        "intended": "the unique application key on core_applicationdecision.application_id",
        "targets": [
            "test_seed_o24_application_terminal_finality.ApplicationTerminalFinalityTests.test_second_decision_is_refused_by_application_uniqueness"
        ],
        "sql": [
            "DO $$\n               DECLARE\n                   application_att smallint;\n                   unique_constraint text;\n                   unique_index oid;\n               BEGIN\n                   SELECT attnum INTO application_att\n                     FROM pg_catalog.pg_attribute\n                    WHERE attrelid = 'public.core_applicationdecision'::regclass\n                      AND attname = 'application_id'\n                      AND NOT attisdropped;\n                   SELECT conname INTO unique_constraint\n                     FROM pg_catalog.pg_constraint\n                    WHERE conrelid = 'public.core_applicationdecision'::regclass\n                      AND contype = 'u'\n                      AND cardinality(conkey) = 1\n                      AND conkey[1] = application_att;\n                   IF unique_constraint IS NOT NULL THEN\n                       EXECUTE format(\n                           'ALTER TABLE public.core_applicationdecision DROP CONSTRAINT %I',\n                           unique_constraint\n                       );\n                   ELSE\n                       SELECT indexrelid INTO unique_index\n                         FROM pg_catalog.pg_index\n                        WHERE indrelid = 'public.core_applicationdecision'::regclass\n                          AND indisunique\n                          AND indnatts = 1\n                          AND pg_get_indexdef(indexrelid, 1, true) = 'application_id';\n                       IF unique_index IS NULL THEN\n                           RAISE EXCEPTION 'application decision unique key was not found';\n                       END IF;\n                       EXECUTE format('DROP INDEX %s', unique_index::regclass);\n                   END IF;\n               END\n               $$"
        ]
    },
    "o24-terminal-race-admission": {
        # ADMISSION: sensitivity to removal of terminal exclusion under contention.
        "follow_up": "O-24 TAR-3 threaded admission evidence",
        "intended": "sensitivity to removal of terminal exclusion under contention; decision first fails the loser's refusal-text assertion, while withdrawal first fails the one-commit assertion; it does not guarantee a two-row witness",
        "targets": [
            "test_seed_o24_application_terminal_finality.ApplicationTerminalFinalityTests.test_threaded_decision_and_withdrawal_leave_one_terminal_row"
        ],
        "sql": [
            "CREATE OR REPLACE FUNCTION public.s3_terminal_lock()\n               RETURNS trigger LANGUAGE plpgsql AS $$\n               BEGIN\n                   PERFORM public.s3_lock_application(NEW.application_id);\n                   NEW.recorded_at := clock_timestamp();\n                   RETURN NEW;\n               END\n               $$"
        ]
    },
    "sr-c2-attribution-gate": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 admission evidence (test 01)",
        "intended": "0052's office-attribution gate: every LO-scoped code is guarded, not COORDINATOR only; test 09 independently detects the wrong installed body, not reverse-refusal sensitivity",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_01_non_coordinator_assign_as_party_act_refused_by_attribution_guard",
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_09_migration_0052_forward_reverse_clean_and_populated_reverse_refused"
        ],
        "sql": [
            "DO $$\n               DECLARE definition text;\n               BEGIN\n                   SELECT pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure)\n                     INTO definition;\n                   IF definition IS NULL\n                      OR position(E'    IF v_role_scope IS DISTINCT FROM ''LIVING_ORGANISM'' THEN\\n        RETURN NEW;' IN definition) = 0 THEN\n                       RAISE EXCEPTION 'expected widened attribution gate was not found';\n                   END IF;\n                   EXECUTE replace(\n                       definition,\n                       E'    IF v_role_scope IS DISTINCT FROM ''LIVING_ORGANISM'' THEN\\n        RETURN NEW;',\n                       E'    IF v_role_code IS DISTINCT FROM ''COORDINATOR''\\n       OR v_role_scope IS DISTINCT FROM ''LIVING_ORGANISM'' THEN\\n        RETURN NEW;'\n                   );\n               END\n               $$"
        ]
    },
    "sr-c2-consent-gate": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 admission evidence (test 02)",
        "intended": "0052's assign-consent gate: an LO-scoped ASSIGN needs the nominee's current consent whatever the code",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_02_non_coordinator_assign_without_consent_refused_by_consent_guard"
        ],
        "sql": [
            "DO $$\n               DECLARE definition text;\n               BEGIN\n                   SELECT pg_get_functiondef('public.s2b_guard_assign_consent()'::regprocedure)\n                     INTO definition;\n                   IF definition IS NULL\n                      OR position(E'       AND role_scope IS DISTINCT FROM ''LIVING_ORGANISM'' THEN' IN definition) = 0 THEN\n                       RAISE EXCEPTION 'expected widened consent gate was not found';\n                   END IF;\n                   EXECUTE replace(\n                       definition,\n                       E'       AND role_scope IS DISTINCT FROM ''LIVING_ORGANISM'' THEN',\n                       E'       AND (role_code IS DISTINCT FROM ''COORDINATOR''\\n            OR role_scope IS DISTINCT FROM ''LIVING_ORGANISM'') THEN'\n                   );\n               END\n               $$"
        ]
    },
    "sr-c2-already-held": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 admission evidence (test 05)",
        "intended": "0052's already-held check in the ASSIGN path of the general branch (H48)",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_05_second_assign_while_office_held_refused",
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_10_concurrent_assigns_of_one_office_leave_one_active_appointment"
        ],
        "sql": [
            "DO $$\n               DECLARE definition text;\n               BEGIN\n                   SELECT pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure)\n                     INTO definition;\n                   IF definition IS NULL\n                      OR position('        IF NEW.action = ''ASSIGN'' AND EXISTS (' IN definition) = 0 THEN\n                       RAISE EXCEPTION 'expected already-held check was not found';\n                   END IF;\n                   EXECUTE replace(\n                       definition,\n                       '        IF NEW.action = ''ASSIGN'' AND EXISTS (',\n                       '        IF FALSE AND EXISTS ('\n                   );\n               END\n               $$"
        ]
    },
    "sr-c2-reserved-root": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 admission evidence (test 07)",
        "intended": "0052's reserved-root refusal at the head of the widened path",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_07_reserved_root_office_act_refused_at_the_guard"
        ],
        "sql": [
            "DO $$\n               DECLARE definition text;\n               BEGIN\n                   SELECT pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure)\n                     INTO definition;\n                   IF definition IS NULL\n                      OR position('    IF starts_with(v_role_code, ''INTEVIA_'') THEN' IN definition) = 0 THEN\n                       RAISE EXCEPTION 'expected reserved-root refusal was not found';\n                   END IF;\n                   EXECUTE replace(\n                       definition,\n                       '    IF starts_with(v_role_code, ''INTEVIA_'') THEN',\n                       '    IF FALSE THEN'\n                   );\n               END\n               $$"
        ]
    },
    "fu-b2-unrelated-reuse-refusal": {
        "follow_up": "FU-B2 (A1 review O-2)",
        "intended": "the reuse guardian's R-d refusal: R-d as ruled and landed (ILC Datacron section 3, What the packet builds, item 2; section 6) and implemented by 0022 RESOLUTION_REUSE_GUARD_BODY; design v0.6 section 5.5 posed the question",
        "targets": [
            "test_s015_0022_contract.S0150022ContractTests.test_reinsert_after_severance_is_refused"
        ],
        "sql": [
            "CREATE OR REPLACE FUNCTION public.s015_0022_guard_resolution_reuse()\n               RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$\n               BEGIN\n                   IF EXISTS (SELECT 1 FROM public.core_identityresolutionsevered s WHERE s.identity_id = NEW.identity_id) THEN\n                       RAISE EXCEPTION 'VERIFICATION MUTATION: an unrelated refusal' USING ERRCODE = 'integrity_constraint_violation';\n                   END IF;\n                   RETURN NEW;\n               END;\n               $$"
        ]
    },
    "g4-direct-activation-without-coverage": {
        "follow_up": "G4 recurring alternate-write control",
        "intended": "A correctly attributed direct ACTIVE with a place and a mandate still needs a qualified Circle Coordinator.",
        "targets": ["test_circle_operations.CircleOperationsTests.test_owned_work_waits_for_an_authorised_parent"],
        "sql": [
            "DO $mutation$\nDECLARE body text; marker text := 'IF NOT EXISTS (SELECT FROM public.circle_holders(c_id,cut,as_known)) THEN';\nBEGIN\n SELECT pg_get_functiondef('public.circle_activation_problem(bigint,bigint,uuid,text,text,timestamptz,timestamptz,timestamptz)'::regprocedure)\n INTO STRICT body;\n IF length(body)-length(replace(body,marker,''))<>length(marker) THEN\n    RAISE EXCEPTION 'G4 recurring fault requires the unique Coordinator coverage check';\n END IF;\n EXECUTE replace(body,marker,'IF false THEN');\nEND $mutation$"
        ],
    },
    "fu-b3-unrelated-circle-check": {
        "follow_up": "FU-B3 (A1 review O-2)",
        "intended": "the circle event resulting-state vocabulary CHECK s015_0021_circleevent_resulting_state_vocab_ck (0021)",
        "targets": [
            "test_s015_0021_negative_direct_sql.VocabulariesAndShapes.test_neg_circle_active_on_event"
        ],
        "sql": [
            "ALTER TABLE public.core_circlestateevent DROP CONSTRAINT s015_0021_circleevent_resulting_state_vocab_ck",
            "ALTER TABLE public.core_circlestateevent ADD CONSTRAINT verification_mutation_unrelated_ck CHECK (resulting_state <> 'UNKNOWN_CIRCLE')"
        ]
    },
    "sr-c2-propose-entitlement": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 ADMISSION (Advisor amended candidate; qualification pending)",
        "intended": "Only the CUSTOM entitlement check is disabled; test 06 fails in its first case",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_06_propose_refused_without_custom_entitlement_and_on_superseded_version"
        ],
        "sql": [
            "DO $mutation$\nDECLARE definition text; needle text := '        IF v_role_kind = ''CUSTOM''';\nBEGIN\n    SELECT pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure) INTO definition;\n    IF definition IS NULL OR\n       (length(definition) - length(replace(definition, needle, ''))) <> length(needle) THEN\n        RAISE EXCEPTION 'expected unique C2 mutation marker was not found';\n    END IF;\n    EXECUTE replace(definition, needle, '        IF FALSE');\nEND\n$mutation$"
        ]
    },
    "sr-c2-propose-superseded": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 ADMISSION (Advisor amended candidate; qualification pending)",
        "intended": "Only inactive-definition refusal is disabled; test 06 reaches and fails in its second case",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_06_propose_refused_without_custom_entitlement_and_on_superseded_version"
        ],
        "sql": [
            "DO $mutation$\nDECLARE definition text; needle text := '        IF v_role_active IS DISTINCT FROM TRUE THEN';\nBEGIN\n    SELECT pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure) INTO definition;\n    IF definition IS NULL OR\n       (length(definition) - length(replace(definition, needle, ''))) <> length(needle) THEN\n        RAISE EXCEPTION 'expected unique C2 mutation marker was not found';\n    END IF;\n    EXECUTE replace(definition, needle, '        IF FALSE THEN');\nEND\n$mutation$"
        ]
    },
    "sr-c2-held-cache-only": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 ADMISSION (Advisor amended candidate; qualification pending)",
        "intended": "Restore the LD cache-only H48 predicate; test 05 must fail on its delayed-refresh case",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_05_second_assign_while_office_held_refused"
        ],
        "sql": [
            "DO $mutation$\nDECLARE definition text; needle text := '               AND (held.state = ''ACTIVE'' OR public.s015_fold_effective_state(\n                   ''core_contextualroleassignment'', held.id,\n                   NEW.effective_at, NEW.recorded_at\n               ) = ''ACTIVE'')';\nBEGIN\n    SELECT pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure) INTO definition;\n    IF definition IS NULL OR\n       (length(definition) - length(replace(definition, needle, ''))) <> length(needle) THEN\n        RAISE EXCEPTION 'expected unique C2 mutation marker was not found';\n    END IF;\n    EXECUTE replace(definition, needle, '               AND held.state = ''ACTIVE''');\nEND\n$mutation$"
        ]
    },
    "sr-c2-late-consent": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 ADMISSION (Advisor amended candidate; qualification pending)",
        "intended": "Disable only consent-history-after-appointment refusal; the valid next WITHDRAW must be admitted",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_02_non_coordinator_assign_without_consent_refused_by_consent_guard"
        ],
        "sql": [
            "DO $mutation$\nDECLARE definition text; needle text := '    IF EXISTS (\n        SELECT 1 FROM public.core_contextualroleassignmentstateevent assigned\n';\nBEGIN\n    SELECT pg_get_functiondef('public.s2b_guard_office_consent()'::regprocedure) INTO definition;\n    IF definition IS NULL OR\n       (length(definition) - length(replace(definition, needle, ''))) <> length(needle) THEN\n        RAISE EXCEPTION 'expected unique C2 mutation marker was not found';\n    END IF;\n    EXECUTE replace(definition, needle, '    IF FALSE AND EXISTS (\n        SELECT 1 FROM public.core_contextualroleassignmentstateevent assigned\n');\nEND\n$mutation$"
        ]
    },
    "sr-c2-lawful-chain-overrefusal": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 ADMISSION (Advisor amended candidate; qualification pending)",
        "intended": "Wrongly refuse an otherwise lawful active non-Coordinator definition; test 03 must assert lawful admission",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_03_lawful_non_coordinator_chain_commits_and_coordinator_conditions_verbatim"
        ],
        "sql": [
            "DO $mutation$\nDECLARE definition text; needle text := '        IF v_role_active IS DISTINCT FROM TRUE THEN';\nBEGIN\n    SELECT pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure) INTO definition;\n    IF definition IS NULL OR\n       (length(definition) - length(replace(definition, needle, ''))) <> length(needle) THEN\n        RAISE EXCEPTION 'expected unique C2 mutation marker was not found';\n    END IF;\n    EXECUTE replace(definition, needle, '        IF v_role_active IS DISTINCT FROM TRUE OR v_role_code <> ''COORDINATOR'' THEN');\nEND\n$mutation$"
        ]
    },
    "sr-c2-founder-protection": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 ADMISSION (Advisor amended candidate; qualification pending)",
        "intended": "Disable only founder protection; test 04 must fail because the protected END is admitted",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_04_founder_held_non_coordinator_office_not_caught_by_founder_protection"
        ],
        "sql": [
            "DO $mutation$\nDECLARE definition text; needle text := '    IF NEW.action IN (''END'', ''REASSIGN'')\n       AND v_role_code = ''COORDINATOR''';\nBEGIN\n    SELECT pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure) INTO definition;\n    IF definition IS NULL OR\n       (length(definition) - length(replace(definition, needle, ''))) <> length(needle) THEN\n        RAISE EXCEPTION 'expected unique C2 mutation marker was not found';\n    END IF;\n    EXECUTE replace(definition, needle, '    IF FALSE AND NEW.action IN (''END'', ''REASSIGN'')\n       AND v_role_code = ''COORDINATOR''');\nEND\n$mutation$"
        ]
    },
    "sr-c2-held-cross-organism": {
        # ADMISSION: pending PG17 qualification; route currently runs all entries.
        "follow_up": "SR C2 ADMISSION (Advisor amended candidate; qualification pending)",
        "intended": "One predicate defect: match holdings of the same code in other organisms regardless of membership; test 08 must reject the over-refusal",
        "targets": [
            "test_sr_office_guards.SROfficeGuardTests.test_sr_c2_08_circle_scope_unguarded_and_other_holdings_do_not_count_as_held"
        ],
        "sql": [
            "DO $mutation$\nDECLARE definition text; needle text := '             WHERE held.membership_id = v_membership_id\n               AND held.id <> NEW.assignment_id\n               AND (held.state = ''ACTIVE'' OR public.s015_fold_effective_state(\n                   ''core_contextualroleassignment'', held.id,\n                   NEW.effective_at, NEW.recorded_at\n               ) = ''ACTIVE'')\n               AND held_definition.living_organism_id = v_lo_id';\nBEGIN\n    SELECT pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure) INTO definition;\n    IF definition IS NULL OR\n       (length(definition) - length(replace(definition, needle, ''))) <> length(needle) THEN\n        RAISE EXCEPTION 'expected unique C2 mutation marker was not found';\n    END IF;\n    EXECUTE replace(definition, needle, '             WHERE (held.membership_id = v_membership_id OR\n                    held_definition.living_organism_id <> v_lo_id)\n               AND held.id <> NEW.assignment_id\n               AND (held.state = ''ACTIVE'' OR public.s015_fold_effective_state(\n                   ''core_contextualroleassignment'', held.id,\n                   NEW.effective_at, NEW.recorded_at\n               ) = ''ACTIVE'')');\nEND\n$mutation$"
        ]
    },
    "sr-c3-citations-admission": {
        # ADMISSION: source faults for Python surfaces are separately declared, never fake SQL.
        "class": 'ADMISSION',
        "follow_up": 'SR C3 admission; unqualified until normal / one-defect / restored evidence',
        "intended": 'Command reaches sr_definition_citations_guard at the outer commit and distinguishes its named refusal',
        "targets": ['test_sr_definitions.SRDefinitionCommandTests.test_07_sr30_reached_at_real_command_commit'],
        "sql": ["DO $mutation$\nDECLARE definition text; needle text := 'SR definition refused: office basis citation missing';\nBEGIN\n SELECT pg_get_functiondef('public.sr_definition_citations_guard()'::regprocedure) INTO definition;\n IF definition IS NULL OR (length(definition)-length(replace(definition,needle,''))) <> length(needle) THEN\n  RAISE EXCEPTION 'C3 expected single guard message missing';\n END IF;\n EXECUTE replace(definition,needle,'C3 MUTATION: unrelated refusal');\nEND\n$mutation$"],
    },
    "sr-c3-supersession-admission": {
        # ADMISSION: source faults for Python surfaces are separately declared, never fake SQL.
        "class": 'ADMISSION',
        "follow_up": 'SR C3 admission; unqualified until normal / one-defect / restored evidence',
        "intended": 'Command reaches sr_definition_superseded_guard at the outer commit and distinguishes its named refusal',
        "targets": ['test_sr_definitions.SRDefinitionCommandTests.test_07_sr30_reached_at_real_command_commit'],
        "sql": ["DO $mutation$\nDECLARE definition text; needle text := 'SR definition refused: a deactivated version must be superseded by a successor at commit';\nBEGIN\n SELECT pg_get_functiondef('public.sr_definition_superseded_guard()'::regprocedure) INTO definition;\n IF definition IS NULL OR (length(definition)-length(replace(definition,needle,''))) <> length(needle) THEN\n  RAISE EXCEPTION 'C3 expected single guard message missing';\n END IF;\n EXECUTE replace(definition,needle,'C3 MUTATION: unrelated refusal');\nEND\n$mutation$"],
    },
}


def selected_mutation(name):
    """Resolve an explicit mutation; SR-H admission entries never enter the recurring list."""
    from verification.admission_mutations import ADMISSION_MUTATIONS
    if set(MUTATIONS) & set(ADMISSION_MUTATIONS):
        raise RuntimeError("mutation registries have duplicate names; reconcile them before running")
    if name in MUTATIONS:
        return MUTATIONS[name]
    if name in ADMISSION_MUTATIONS:
        entry = ADMISSION_MUTATIONS[name]
        if entry.get("class") != "ADMISSION":
            raise RuntimeError("the separate registry accepts admission experiments only")
        return entry
    raise RuntimeError("unknown or missing %s: %r" % (MUTATION_ENV, name))


class MutationRunner(RecordingRunnerMixin, TemplateModeRunnerMixin, OwnedDatabaseRunnerMixin, DiscoverRunner):
    """Applies the mutation named in VERIFICATION_MUTATION to the test database (a clone of the run's template in clone
    mode; a fresh build only under a Human-authorised fresh mode), before any test."""

    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        name = os.environ.get(MUTATION_ENV)
        entry = selected_mutation(name)
        if getattr(self, "template_mode", "clone") == "clone":
            self.verify_clone_matches_template()
        with connection.cursor() as cursor:
            for statement in entry["sql"]:
                cursor.execute(statement)
        print("VERIFICATION MUTATION APPLIED: %s" % name, flush=True)
        return old_config

    def verify_clone_matches_template(self):
        """CI-CLONE-1: before any mutation statement, the clone's CP-0 must equal the template's recorded fresh-build CP-0.

        The clone is a copy of the template at the moment it was taken, so an equal digest shows the compared CP-0
        properties matched the fresh-build reference at that moment. The comparison is written as a clone_cp0_verified receipt (read by the route) and
        printed as TT1_CLONE_CP0_MATCHES_TEMPLATE. A difference refuses the step before the mutation is applied; the
        route never falls back to a fresh build (R9).
        """
        from verification.isolation import state
        from verification.isolation.runner import _runner_print

        receipt = getattr(self, "template_receipt", None)
        if not isinstance(receipt, dict) or not receipt.get("cp0_digest"):
            raise RuntimeError("R9: clone mode has no template receipt to verify the clone against; fresh-mode fallback is prohibited")
        started = time.perf_counter()
        clone_digest = state.digest(state.capture())
        matches = clone_digest == receipt["cp0_digest"]
        write_receipt("clone_cp0_verified", name=connection.settings_dict.get("NAME"), template_name=receipt.get("name"),
                      template_oid=receipt.get("oid"), template_cp0_digest=receipt["cp0_digest"],
                      clone_cp0_digest=clone_digest, matches=matches, seconds=round(time.perf_counter() - started, 6))
        _runner_print("TT1_CLONE_CP0_MATCHES_TEMPLATE=%s" % matches, file=sys.stderr, flush=True)
        if not matches:
            raise RuntimeError(
                "R9: the mutation check could not start because the clone's recorded starting state differs from the "
                "fresh-build reference. No mutation SQL or target test was run; this step's database was not changed. "
                "Preserve this run's evidence and ask the VC/ME to inspect the template and clone records (the receipts under "
                "PKTB_VERIFICATION_ROUTE) before releasing another run. A fresh database will not be substituted. "
                "Technical details: clone CP-0 %s, fresh-build reference %s" % (clone_digest, receipt["cp0_digest"])
            )
