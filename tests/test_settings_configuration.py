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