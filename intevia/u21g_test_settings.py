"""U21g Phase 2 test settings — runs the S015 0021 test modules against the throwaway database ONLY.

Differences from intevia.test_settings (which requires a `test_intevia_living_organism_*` database the U21g mandate does
not permit): the test database is the existing throwaway named by INTEVIA_POSTGRES_TEST_DB and is kept (--keepdb);
the flush between TransactionTestCase tests disables every S015 TRUNCATE guardian on the flushed tables (0019's closed
list and 0021's `s015_0021_*_truncate_*` set) and re-enables them afterwards.
"""
from .settings import *  # noqa: F401,F403

DATABASES["default"]["ENGINE"] = "intevia.u21g_test_backend"  # noqa: F405
