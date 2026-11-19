"""U-D P2: the fixture-terms database families (rule page G9). No SQL is run.

UD-05 (T6): ``fixture_terms_permitted`` admits two families of disposable database and
no other name. The test family is unchanged; the Alpha family was added by the Human
Governor's ruling of 19 November 2026. Expected verdicts are written here, name by name.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.db import connections
from django.test import SimpleTestCase

from src.intevia.services import organism_application_authority as authority

# The same valid Alpha names the Alpha settings' own gate admits (tests/test_ud_alpha_settings.py).
ALPHA_NAMES = (
    "intevia_alpha_throwaway_film1",
    "intevia_alpha_throwaway_a",
    "intevia_alpha_throwaway_0",
    "intevia_alpha_throwaway__",
    "intevia_alpha_throwaway_film_2026_11",
)
# The legacy condition is a prefix and a non-empty suffix of any characters.
LEGACY_NAMES = (
    "test_intevia_living_organism_a1",
    "test_intevia_living_organism_tt1_me_7",
    "test_intevia_living_organism_UPPER",
    "test_intevia_living_organism_a-b",
    "test_intevia_living_organism_x.y",
)
PROTECTED_NAMES = (
    "intevia_production",
    "intevia",
    "intevia_beta",
    "intevia_beta_throwaway_a",
    "intevia_peek_throwaway_a",
    "postgres",
    "",
)
BARE_PREFIXES = (
    "intevia_alpha_throwaway_",
    "intevia_alpha_throwaway",
    "test_intevia_living_organism_",
    "test_intevia_living_organism",
)
MALFORMED_ALPHA_NAMES = (
    "intevia_alpha_throwaway_A",  # a capital
    "INTEVIA_ALPHA_THROWAWAY_a",
    "intevia_alpha_throwaway_a-b",  # a hyphen
    "intevia_alpha_throwaway_a b",  # a space
    "intevia_alpha_throwaway_a\n",  # a trailing newline
    " intevia_alpha_throwaway_a",
    "intevia_alpha_throwaway_é",  # a letter outside ASCII
    "xintevia_alpha_throwaway_a",  # the prefix inside a longer name
    "my_intevia_alpha_throwaway_a",
    "xtest_intevia_living_organism_a",
)


class _Text(str):
    """A str subclass: the predicate requires the exact built-in type and does not coerce."""


class AlphaDatabaseFamilyTests(SimpleTestCase):
    def permitted(self, name):
        with patch.dict(connections["default"].settings_dict, NAME=name):
            return authority.fixture_terms_permitted("default")

    def test_01_fixture_terms_family_truth_table(self):
        """UD-05: the test family and the Alpha family are admitted; every other name is refused."""
        for name in ALPHA_NAMES:
            self.assertIs(
                self.permitted(name),
                True,
                f"The Alpha database name {name!r} must permit fixture terms (ruling of 19 November 2026).",
            )
        for name in LEGACY_NAMES:
            self.assertIs(
                self.permitted(name),
                True,
                f"The test database name {name!r} must still permit fixture terms: the legacy rule is unchanged.",
            )
        for name in PROTECTED_NAMES:
            self.assertIs(
                self.permitted(name),
                False,
                f"The protected database name {name!r} must never permit fixture terms.",
            )
        for name in BARE_PREFIXES:
            self.assertIs(
                self.permitted(name),
                False,
                f"The bare prefix {name!r} names no database of either family and must be refused.",
            )
        for name in MALFORMED_ALPHA_NAMES:
            self.assertIs(
                self.permitted(name),
                False,
                f"The name {name!r} is not an Alpha name in full and must be refused.",
            )

        # No coercion: a value that is not exactly a built-in str is refused, whatever it spells.
        for value in (
            None,
            12345,
            b"intevia_alpha_throwaway_a",
            b"test_intevia_living_organism_a",
            _Text("intevia_alpha_throwaway_a"),
            _Text("test_intevia_living_organism_a"),
        ):
            self.assertIs(
                self.permitted(value),
                False,
                f"A configured name of type {type(value).__name__} must be refused without coercion.",
            )
        settings_dict = connections["default"].settings_dict
        with patch.dict(settings_dict):
            del settings_dict["NAME"]
            self.assertIs(
                authority.fixture_terms_permitted("default"),
                False,
                "A database with no configured name must be refused.",
            )

        # The alias asked about is the alias read: never the default connection in its place.
        alpha, protected = ALPHA_NAMES[0], PROTECTED_NAMES[0]
        for default_name, second_name in ((protected, alpha), (alpha, protected)):
            two_aliases = {
                "default": SimpleNamespace(settings_dict={"NAME": default_name}),
                "second": SimpleNamespace(settings_dict={"NAME": second_name}),
            }
            with patch.object(authority, "connections", two_aliases):
                self.assertIs(
                    authority.fixture_terms_permitted("second"),
                    second_name == alpha,
                    f"Asked about the second alias ({second_name!r}), the answer must be that alias's own.",
                )
                self.assertIs(
                    authority.fixture_terms_permitted("default"),
                    default_name == alpha,
                    f"Asked about the default alias ({default_name!r}), the answer must be that alias's own.",
                )
