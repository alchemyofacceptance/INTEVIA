"""
WSGI config for intevia project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intevia.settings')

application = get_wsgi_application()

# The INTEVIA Clock: refuse to serve a database that ever held the test route's moving part, and leave no
# database connection open in this process behind the check: a pre-fork server forks its workers from it.
from core.clock_startup import refuse_at_serving_entry  # noqa: E402

refuse_at_serving_entry()
