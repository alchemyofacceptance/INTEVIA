"""T-1 guard: catch known forms of frozen whole-system assertions.

This is a bounded lint check, not proof that arbitrary Python has no brittle assumption.
Review still checks scope and historical-state setup. Scan support files and nested tests
as well as top-level tests. Numeric row counts of a fixture and historical migration
boundaries remain legitimate; the isolation machinery owns version-bound totals.
"""

import ast
import re
from pathlib import Path

from django.test import SimpleTestCase

TESTS_DIR = Path(__file__).resolve().parent
CONTRACT_NAMES = {"REQUIRED_TRIGGER_COUNT", "MIGRATION_SEED_ROW_COUNTS_BY_HEAD"}
LIVE_CATALOGUES = {"OrganismAction", "IntakeEntry", "QUALIFICATION_PLANE_MATRIX"}
AGGREGATE_NAME = re.compile(r"(?:schema|trigger|catalogue|catalog|identity_fk).*?(?:count|total)|(?:count|total).*?(?:schema|trigger|catalogue|catalog|identity_fk)", re.I)
CURRENT_HEAD = re.compile(r"(?:CURRENT.*HEAD|HEAD.*CURRENT|HEAD.*TODAY|RECEIVED_HEAD|LATEST.*MIGRATION)$", re.I)


def frozen_sites(source):
    tree = ast.parse(source)
    findings = set()
    literals = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                        literals[target.id] = node.value.value
                    if CURRENT_HEAD.search(target.id) and any(
                        isinstance(item, ast.Constant) and isinstance(item.value, str)
                        and re.fullmatch(r"\d{4}_.+", item.value)
                        for item in ast.walk(node.value)
                    ):
                        findings.add((node.lineno, "literal current migration head"))
        name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
        if name in CONTRACT_NAMES or isinstance(node, ast.ImportFrom) and any(a.name in CONTRACT_NAMES for a in node.names):
            findings.add((node.lineno, "ordinary test reads version-bound schema totals"))

    def positive_number(node):
        value = node.value if isinstance(node, ast.Constant) else literals.get(node.id) if isinstance(node, ast.Name) else None
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "assertEqual" and len(node.args) >= 2):
            continue
        for observed, expected in (node.args[:2], node.args[1::-1]):
            if not positive_number(expected):
                continue
            names = {item.id for item in ast.walk(observed) if isinstance(item, ast.Name)}
            names |= {item.attr for item in ast.walk(observed) if isinstance(item, ast.Attribute)}
            is_live_length = (isinstance(observed, ast.Call) and isinstance(observed.func, ast.Name)
                              and observed.func.id == "len" and len(observed.args) == 1
                              and isinstance(observed.args[0], ast.Name)
                              and observed.args[0].id in LIVE_CATALOGUES)
            if is_live_length or any(AGGREGATE_NAME.search(name) for name in names):
                findings.add((node.lineno, "frozen live catalogue/aggregate total"))
    return sorted(findings)


class NoFrozenSchemaTotalsTests(SimpleTestCase):
    def test_no_test_module_reads_the_version_bound_schema_contract(self):
        offenders = []
        for path in sorted(TESTS_DIR.rglob("*.py")):
            if path == Path(__file__).resolve():
                continue
            for line, reason in frozen_sites(path.read_text(encoding="utf-8-sig")):
                offenders.append(f"{path.relative_to(TESTS_DIR)}:{line}: {reason}")
        self.assertEqual(offenders, [], "assert named safeguards or historical deltas, not frozen live totals")

    def test_guard_rejects_direct_totals_alias_imports_and_current_heads(self):
        examples = (
            "self.assertEqual(_trigger_count(), 475)",
            "TOTAL = 475\nself.assertEqual(trigger_count, TOTAL)",
            "self.assertEqual(len(OrganismAction), 35)",
            "self.assertEqual(23, len(IntakeEntry))",
            "from verification.isolation.state import REQUIRED_TRIGGER_COUNT as expected",
            "CURRENT_CORE_HEAD = ('core', '0051_sr_role_definitions')",
        )
        for source in examples:
            with self.subTest(source=source):
                self.assertTrue(frozen_sites(source))

    def test_guard_allows_historical_boundaries_deltas_and_fixture_counts(self):
        examples = (
            "PREVIOUS = ('core', '0047_sek1_platform_rule_statement_start')",
            "self.assertEqual(len(rows), 2)",
            "self.assertEqual(trigger_count, before + 1)",
            "self.assertEqual(disabled_trigger_count, 0)",
            "# REQUIRED_TRIGGER_COUNT is documentation, not code\npass",
        )
        for source in examples:
            with self.subTest(source=source):
                self.assertEqual(frozen_sites(source), [])
