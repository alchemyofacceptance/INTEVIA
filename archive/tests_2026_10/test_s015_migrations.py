import importlib
import inspect
from django.test import SimpleTestCase


class S015MigrationContractTests(SimpleTestCase):
    def test_migration_is_schema_only_and_depends_on_0018(self):
        module = importlib.import_module(
            "core.migrations.0019_s015_living_organism_foundation"
        )
        self.assertEqual(
            module.Migration.dependencies,
            [("core", "0018_s014_education_course_foundation")],
        )
        source = inspect.getsource(module)
        self.assertNotIn("RunSQL", source)
        self.assertNotIn("objects.create", source)
        self.assertIn("install_s015_postgresql_guardians", source)

    def test_guardian_23_inventory_is_closed(self):
        module = importlib.import_module(
            "core.migrations.0019_s015_living_organism_foundation"
        )
        self.assertEqual(len(module.IMMUTABLE_ANCHORS), 8)
        self.assertEqual(sum(map(len, module.IMMUTABLE_ANCHORS.values())), 51)
