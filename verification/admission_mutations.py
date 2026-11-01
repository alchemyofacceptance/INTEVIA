"""Explicit SR-H admission experiments, excluded from the ordinary CI registry.

Run one named entry at a time in MutationRunner's owned disposable clone, after
normal PASS; require assertion FAIL without ERROR, then restore and require PASS.
The INTEVIA update guard has no lawful physical fixture yet (FW-11): its missing
admission experiment is disclosed in the SR-H ledger, not registered as runnable.
The explicit selector in verification/mutations.py imports this registry.
No import executes SQL; no entry runs unless named. The ordinary 19 mutation
entries never include these admission entries.
"""

ADMISSION_MUTATIONS = {'sr-h-stale-assign': {'class': 'ADMISSION',
                       'intended': "IF NEW.action = 'ASSIGN' AND v_role_active IS DISTINCT FROM TRUE THEN",
                       'follow_up': 'SR-H once-only admission; not part of ordinary route',
                       'targets': ['test_sr_h_safeguards.SROfficeSafeguardTests.test_01_stale_assign_is_refused_by_database'],
                       'sql': ['DO $srh$ DECLARE definition text; needle text := $needle$IF NEW.action = '
                               "'ASSIGN' AND v_role_active IS DISTINCT FROM TRUE THEN$needle$;\n"
                               'BEGIN\n'
                               '    SELECT '
                               "pg_get_functiondef('public.s2b_guard_office_attribution()'::regprocedure) "
                               'INTO definition;\n'
                               '    IF definition IS NULL OR (length(definition)-length(replace(definition, '
                               "needle, '')))/length(needle) <> 1 THEN\n"
                               "        RAISE EXCEPTION 'SR-H admission fault cannot find exactly one "
                               "intended clause';\n"
                               '    END IF;\n'
                               "    EXECUTE replace(definition, needle, 'IF FALSE THEN');\n"
                               'END $srh$']},
 'sr-h-coordinator-insert': {'class': 'ADMISSION',
                             'intended': "IF NEW.code = 'COORDINATOR' THEN",
                             'follow_up': 'SR-H once-only admission; not part of ordinary route; one-defect '
                                          'break: the explicit Coordinator primary is no longer raised; a '
                                          'later decision-mismatch refusal may then appear.',
                             'targets': ['test_sr_h_safeguards.SROfficeSafeguardTests.test_02_coordinator_definition_cannot_be_replaced_or_retired'],
                             'sql': ['DO $srh$ DECLARE definition text; needle text := $needle$IF NEW.code = '
                                     "'COORDINATOR' THEN$needle$;\n"
                                     'BEGIN\n'
                                     '    SELECT '
                                     "pg_get_functiondef('public.sr_definition_insert_guard()'::regprocedure) "
                                     'INTO definition;\n'
                                     '    IF definition IS NULL OR '
                                     '(length(definition)-length(replace(definition, needle, '
                                     "'')))/length(needle) <> 1 THEN\n"
                                     "        RAISE EXCEPTION 'SR-H admission fault cannot find exactly one "
                                     "intended clause';\n"
                                     '    END IF;\n'
                                     "    EXECUTE replace(definition, needle, 'IF FALSE THEN');\n"
                                     'END $srh$']},
 'sr-h-coordinator-update': {'class': 'ADMISSION',
                             'intended': "IF OLD.code = 'COORDINATOR' THEN",
                             'follow_up': 'SR-H once-only admission; not part of ordinary route; one-defect '
                                          'break: the explicit Coordinator primary is no longer raised; a '
                                          'later decision-mismatch refusal may then appear.',
                             'targets': ['test_sr_h_safeguards.SROfficeSafeguardTests.test_02_coordinator_definition_cannot_be_replaced_or_retired'],
                             'sql': ['DO $srh$ DECLARE definition text; needle text := $needle$IF OLD.code = '
                                     "'COORDINATOR' THEN$needle$;\n"
                                     'BEGIN\n'
                                     '    SELECT '
                                     "pg_get_functiondef('public.sr_definition_update_guard()'::regprocedure) "
                                     'INTO definition;\n'
                                     '    IF definition IS NULL OR '
                                     '(length(definition)-length(replace(definition, needle, '
                                     "'')))/length(needle) <> 1 THEN\n"
                                     "        RAISE EXCEPTION 'SR-H admission fault cannot find exactly one "
                                     "intended clause';\n"
                                     '    END IF;\n'
                                     "    EXECUTE replace(definition, needle, 'IF FALSE THEN');\n"
                                     'END $srh$']},
 'sr-h-board-retire': {'class': 'ADMISSION',
                       'intended': "IF OLD.kind = 'BOARD_OFFICE' THEN",
                       'follow_up': 'SR-H once-only admission; not part of ordinary route',
                       'targets': ['test_sr_h_safeguards.SROfficeSafeguardTests.test_03_board_retirement_is_refused_but_custom_retirement_commits'],
                       'sql': ['DO $srh$ DECLARE definition text; needle text := $needle$IF OLD.kind = '
                               "'BOARD_OFFICE' THEN$needle$;\n"
                               'BEGIN\n'
                               '    SELECT '
                               "pg_get_functiondef('public.sr_definition_update_guard()'::regprocedure) INTO "
                               'definition;\n'
                               '    IF definition IS NULL OR (length(definition)-length(replace(definition, '
                               "needle, '')))/length(needle) <> 1 THEN\n"
                               "        RAISE EXCEPTION 'SR-H admission fault cannot find exactly one "
                               "intended clause';\n"
                               '    END IF;\n'
                               "    EXECUTE replace(definition, needle, 'IF FALSE THEN');\n"
                               'END $srh$']},
 'sr-h-kind-fixed': {'class': 'ADMISSION',
                     'intended': 'IF v_superseded.id IS NOT NULL AND NEW.kind IS DISTINCT FROM '
                                 'v_superseded.kind THEN',
                     'follow_up': 'SR-H once-only admission; not part of ordinary route',
                     'targets': ['test_sr_h_safeguards.SROfficeSafeguardTests.test_04_successor_kind_is_fixed_in_both_directions'],
                     'sql': ['DO $srh$ DECLARE definition text; needle text := $needle$IF v_superseded.id IS '
                             'NOT NULL AND NEW.kind IS DISTINCT FROM v_superseded.kind THEN$needle$;\n'
                             'BEGIN\n'
                             '    SELECT '
                             "pg_get_functiondef('public.sr_definition_insert_guard()'::regprocedure) INTO "
                             'definition;\n'
                             '    IF definition IS NULL OR (length(definition)-length(replace(definition, '
                             "needle, '')))/length(needle) <> 1 THEN\n"
                             "        RAISE EXCEPTION 'SR-H admission fault cannot find exactly one intended "
                             "clause';\n"
                             '    END IF;\n'
                             "    EXECUTE replace(definition, needle, 'IF FALSE THEN');\n"
                             'END $srh$']},
 'sr-h-successor-latest': {'class': 'ADMISSION',
 'intended': "IF v_max_version > 0 AND NEW.code <> 'COORDINATOR' THEN",
 'follow_up': 'SR-H once-only admission; not part of ordinary route',
 'targets': ['test_sr_h_safeguards.SROfficeSafeguardTests.test_08_retired_code_cannot_restart_without_latest_predecessor'],
 'sql': ['DO $srh$ DECLARE definition text; needle text := $needle$IF v_max_version > 0 AND '
         "NEW.code <> 'COORDINATOR' THEN$needle$;\n"
         'BEGIN\n'
         "    SELECT pg_get_functiondef('public.sr_definition_insert_guard()'::regprocedure) INTO "
         'definition;\n'
         '    IF definition IS NULL OR (length(definition)-length(replace(definition, needle, '
         "'')))/length(needle) <> 1 THEN\n"
         "        RAISE EXCEPTION 'SR-H admission fault cannot find exactly one intended clause';\n"
         '    END IF;\n'
         "    EXECUTE replace(definition, needle, 'IF FALSE THEN');\n"
         'END $srh$']}}
