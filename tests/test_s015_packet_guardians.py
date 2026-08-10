from pathlib import Path
from django.test import SimpleTestCase


class S015PacketBoundaryTests(SimpleTestCase):
    def test_authorized_packet_a_paths_exist_and_forbidden_services_do_not(self):
        root = Path(__file__).resolve().parents[1]
        authorized = {
            "core/models.py",
            "core/organism_contract.py",
            "core/migrations/0019_s015_living_organism_foundation.py",
            "src/intevia/services/__init__.py",
            "src/intevia/services/organism_authority.py",
            "src/intevia/services/organism_membership_service.py",
            "tests/test_s015_models.py",
            "tests/test_s015_migrations.py",
            "tests/test_s015_authority.py",
            "tests/test_s015_membership_service.py",
            "tests/test_s015_postgresql_guardians.py",
            "tests/test_s015_packet_guardians.py",
        }
        self.assertEqual(len(authorized), 12)
        self.assertTrue(all((root / path).is_file() for path in authorized))
        self.assertFalse(
            (root / "src/intevia/services/organism_governance_service.py").exists()
        )
        self.assertFalse((root / "src/intevia/services/organism_queries.py").exists())
