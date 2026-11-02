"""Loopback-only EM-a view; inherits peek's disposable/local-database checks."""
from .peek_settings import *  # noqa: F403

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
