import os
import secrets
import subprocess
import sys

from django.test import SimpleTestCase


class SecretKeyConfigurationTests(SimpleTestCase):
    @staticmethod
    def settings_import(environment):
        return subprocess.run(
            [sys.executable, "-c", "import intevia.settings"],
            cwd=os.getcwd(),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_operational_settings_reject_missing_secret(self):
        environment = os.environ.copy()
        environment.pop("DJANGO_SECRET_KEY", None)

        result = self.settings_import(environment)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY is required", result.stderr)

    def test_operational_settings_accept_ephemeral_secret(self):
        environment = os.environ.copy()
        environment["DJANGO_SECRET_KEY"] = secrets.token_urlsafe(50)

        result = self.settings_import(environment)

        self.assertEqual(result.returncode, 0, result.stderr)


class S015TestDatabaseConfigurationTests(SimpleTestCase):
    @staticmethod
    def settings_import(module, environment):
        return subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    f"from {module} import DATABASES; "
                    "print(DATABASES['default']['ENGINE'])"
                ),
            ],
            cwd=os.getcwd(),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def postgresql_environment():
        environment = os.environ.copy()
        environment.update(
            {
                "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
                "INTEVIA_DATABASE_ENGINE": "postgresql",
                "INTEVIA_POSTGRES_DB": "intevia_living_organism_control",
                "INTEVIA_POSTGRES_USER": "intevia",
                "INTEVIA_POSTGRES_PASSWORD": "test-only-password",
                "INTEVIA_POSTGRES_TEST_DB": (
                    "test_intevia_living_organism_settings"
                ),
            }
        )
        environment.pop("INTEVIA_S015_TEST_ROUTE", None)
        return environment

    def test_production_settings_keep_standard_postgresql_backend(self):
        environment = self.postgresql_environment()
        environment["INTEVIA_S015_TEST_ROUTE"] = (
            "LIVING_ORGANISM_TEST_LIFECYCLE"
        )

        result = self.settings_import("intevia.settings", environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "django.db.backends.postgresql")

    def test_test_settings_keep_standard_backend_without_route(self):
        result = self.settings_import(
            "intevia.test_settings", self.postgresql_environment()
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "django.db.backends.postgresql")

    def test_test_settings_reject_insufficiently_qualified_database(self):
        environment = self.postgresql_environment()
        environment["INTEVIA_S015_TEST_ROUTE"] = (
            "LIVING_ORGANISM_TEST_LIFECYCLE"
        )
        environment["INTEVIA_POSTGRES_TEST_DB"] = "test_intevia"

        result = self.settings_import("intevia.test_settings", environment)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "S015 test lifecycle backend requires the exact attempt route",
            result.stderr,
        )

    def test_test_settings_select_backend_for_exact_disposable_route(self):
        environment = self.postgresql_environment()
        environment["INTEVIA_S015_TEST_ROUTE"] = (
            "LIVING_ORGANISM_TEST_LIFECYCLE"
        )

        result = self.settings_import("intevia.test_settings", environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "intevia.test_postgresql_backend")