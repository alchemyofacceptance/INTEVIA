"""Settings used only by the Django test command."""

import os
import secrets


os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(50))

from .settings import *  # noqa: E402,F403