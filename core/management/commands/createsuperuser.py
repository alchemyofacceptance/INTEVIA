"""Use the ordinary administrator setup with the INTEVIA name reservation."""

from django.contrib.auth.management.commands.createsuperuser import (
    Command as DjangoCommand,
)

from core.care_identity import RESERVED_NAME, reserved_name


class Command(DjangoCommand):
    def _validate_username(self, username, verbose_field_name, database):
        if reserved_name(username):
            return RESERVED_NAME
        return super()._validate_username(username, verbose_field_name, database)
