"""Settings used only by the Django test command."""

import os
import secrets


os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(50))

from .settings import *  # noqa: E402,F403


S015_TEST_ROUTE = "LIVING_ORGANISM_TEST_LIFECYCLE"
S015_DATABASE_PREFIX = "test_intevia_living_organism_"

requested_s015_route = os.environ.get("INTEVIA_S015_TEST_ROUTE")
if requested_s015_route:
	configured_database = DATABASES["default"]
	test_database = configured_database.get("TEST", {}).get("NAME", "")
	if (
		requested_s015_route != S015_TEST_ROUTE
		or configured_database["ENGINE"] != "django.db.backends.postgresql"
		or not test_database.startswith(S015_DATABASE_PREFIX)
		or test_database == S015_DATABASE_PREFIX
	):
		from django.core.exceptions import ImproperlyConfigured

		raise ImproperlyConfigured(
			"S015 test lifecycle backend requires the exact attempt route and "
			"an attempt-owned disposable PostgreSQL test database"
		)
	configured_database["ENGINE"] = "intevia.test_postgresql_backend"
	configured_database["S015_TEST_ROUTE"] = requested_s015_route
