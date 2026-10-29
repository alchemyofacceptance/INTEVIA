# Archived test suite — record (29 October 2026)

This directory holds the INTEVIA test suite as it stood on main `429c9bc57cc46a7f85712469b49017cc0e094fca`,
set down by the Human Governor's decision of 29 October 2026 (HG_DECISIONS_TCR1_FRESH_START_FOUNDATION_20261029).
Every file was moved here with `git mv` from `tests/`; history is in git. Nothing here is collected, imported or
run by the verification route or CI: the directory is outside `tests/`, has no `__init__.py`, and is on no path.
Reuse of any file is by choice only, under the six test rules; nothing obliges anyone to repair or maintain it.

## One-off report (the last whole-suite result)

GitHub Actions run 40552391751 on this same commit: 1,796 tests, PASS, 46 min 37 s. No further run is scheduled.

## Permitted skips preserved as records (formerly verification/permitted_skips.json)

These entries are no longer policy. The active policy file holds no entries. They are kept here so the reasons,
and the ruling below, stay findable.

1. id: tests.test_s007_authentication_shell.AuthenticationShellTests.test_stale_epoch_and_absolute_age_flush_sessions
   condition: always
   reason: H82_HOST_CLOCK_SESSION_AGE: production compares against host time.time(); deferred until Alpha.
2. id: tests.test_sbg_governance.SBGGovernanceDepartureCascadeTests.test_w_d3_5_departure_vacates_the_seat_for_equal_time_succession
   condition: always
   reason: Human Governor ruling 13 Oct 2026: never run again (clock design; ledger H82).
3. id: tests.test_s015_application_authority.ProductionApplicationAuthorityTests.test_ap6_non_superuser_application_role_when_present
   condition: always
   reason: AP-6 requires the non-superuser application role; both branches skip when it is absent.
4. id: tests.test_tt1_template_lifecycle.TemplateLifecycleToolingTests.test_template_clone_parity_and_receipt_cleanup
   condition: fresh-mode
   reason: Clone parity and receipt cleanup are local development concerns only. CI runs fresh mode and is the sole
   evidence of record under the Human Governor's 'proven on GitHub only' ruling. The skip is permitted in fresh mode;
   the property is not claimed as established by any local run.

## NEVER EXECUTE — standing ruling

Human Governor ruling, 13 October 2026: test_w_d3_5_departure_vacates_the_seat_for_equal_time_succession
(now archive/tests_2026_10/test_sbg_governance.py, class SBGGovernanceDepartureCascadeTests) is never to be run
again (clock design; ledger H82). It must not be moved back into tests/, copied into any active suite, or named
as a label, plan entry or mutation target. This record travels with the file.

## Obsolete

scripts/qualify_tu2a.ps1 names archived tests (lines 433-545 at 429c9bc). It is local, not on the CI path, and is
obsolete from this commit; it was not moved.

Also obsolete from this commit, for the same reason (local, not on the CI path, not moved; found at source by the
Vision Chamber): scripts/run_tt1_dev.ps1, scripts/verify_pktb_aa_r1.ps1, scripts/verify_pktb_aa_r2.ps1,
scripts/verify_pktb_aa_r3.ps1 and scripts/verify_tt1.ps1 name archived tests; scripts/freeze_s015_facts_corpus.py
names tests/fixtures, now archive/tests_2026_10/fixtures. The local -LaunchSelfTest switch of
scripts/run_integrated_dev.ps1 (lines 380-382) refuses an empty skip policy and is not to be used until a later unit
corrects it; normal local runs accept the empty policy.
