"""Explicit home-network peek settings, never a deployment settings module."""
import ipaddress
import os
from django.core.exceptions import ImproperlyConfigured
from .settings import *  # noqa: F403

host = os.environ.get("INTEVIA_PEEK_HOST", "127.0.0.1")
try:
    address = ipaddress.ip_address(host)
except ValueError as exc:
    raise ImproperlyConfigured("INTEVIA_PEEK_HOST must be your private LAN IPv4 address.") from exc
if address.version != 4 or not (address.is_private and not address.is_unspecified):
    raise ImproperlyConfigured("The peek server is for a private IPv4 address only.")
if not DATABASES["default"]["NAME"].startswith("intevia_peek_throwaway_"):  # noqa: F405
    raise ImproperlyConfigured("Use a disposable database named intevia_peek_throwaway_<label>.")
if DATABASES["default"].get("HOST", "") not in {"", "localhost", "127.0.0.1", "::1"}:  # noqa: F405
    raise ImproperlyConfigured("The peek database host must be local: empty, localhost, 127.0.0.1 or ::1. No connection was opened; choose the local disposable clone.")
DEBUG = True
ALLOWED_HOSTS = [host,"127.0.0.1","localhost"]
CSRF_TRUSTED_ORIGINS = [f"http://{host}:8000"]
INTEVIA_DESIGN_CATALOGUE = True
