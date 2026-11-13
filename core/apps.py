from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        """Wire the INTEVIA Clock's startup refusal to every database connection."""
        from core import clock_startup

        clock_startup.connect()
