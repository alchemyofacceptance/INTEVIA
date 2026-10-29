import ast
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_FILES = (
    ROOT / "src/intevia/services/library_exact_version_contract.py",
    ROOT / "src/intevia/services/library_exact_version_policy.py",
)


class NoMigrationBoundaryTests(TestCase):
    def test_s011a_has_no_model_or_migration(self):
        migration_names = {path.name.lower() for path in (ROOT / "core/migrations").glob("*.py")}
        self.assertFalse(any("exact_version" in name or "s011a" in name for name in migration_names))
        for path in PRODUCTION_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
            self.assertFalse(any(any(getattr(base, "attr", "") == "Model" for base in node.bases) for node in classes))

    def test_production_boundary_has_no_event_import_or_generic_engine(self):
        for path in PRODUCTION_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            self.assertFalse(any("event" in name.lower() for name in imports), path)
            self.assertNotIn("PolicyEngine", path.read_text(encoding="utf-8"))

    def test_policy_file_remains_explicitly_inactive(self):
        policy = (ROOT / "governance/policies/LIB-EXACT-VERSION-PREALPHA-001-v1.md").read_text(encoding="utf-8")
        self.assertIn("DRAFT, NOT ACTIVE, NOT DEPLOYED, and NOT ENABLED", policy)
        self.assertIn("Policy activation requires separate explicit Human authority", policy)
