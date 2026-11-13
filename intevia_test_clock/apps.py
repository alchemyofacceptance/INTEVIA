from django.apps import AppConfig


class InteviaTestClockConfig(AppConfig):
    """Holds one migration and no model. Production's settings never name this app."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'intevia_test_clock'
