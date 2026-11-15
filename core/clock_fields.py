"""Model fields whose automatic time comes from the INTEVIA Clock.

Django fills a ``DateTimeField(auto_now_add=True)`` or ``(auto_now=True)`` from the web
server's clock. ``ClockDateTimeField`` is the same field with one difference: the automatic
value is the INTEVIA Clock's instant, read through the Python door
(``src.intevia.services.clock.database_now``) on the connection the row is being written to.

Everything else is Django's: when the value is filled (``auto_now_add`` on the first save,
``auto_now`` on every save that writes the field, so a save with ``update_fields`` fills it
only if the field is named), that ``QuerySet.update`` and ``bulk_update`` do not fill it, that
the field is not editable, and that after a save or a ``bulk_create`` the instance holds the
value that was written. The column is the same column: changing a model to this field changes
no table.
"""

from django.db import models


class _ClockRead:
    """Stands in for a value between Django asking a field for it and the write that needs it.

    Django asks a field for its value (``pre_save``) before it has said which connection the
    row goes to, and prepares the value for that connection (``get_db_prep_save``) afterwards.
    The clock is read at the second step, on that connection; this object carries the instance
    across so that the instance can be given the value read.
    """

    __slots__ = ("instance", "attname")

    def __init__(self, instance, attname):
        self.instance = instance
        self.attname = attname


class ClockDateTimeField(models.DateTimeField):
    """A ``DateTimeField`` whose ``auto_now`` and ``auto_now_add`` read the INTEVIA Clock."""

    def pre_save(self, model_instance, add):
        if self.auto_now or (self.auto_now_add and add):
            return _ClockRead(model_instance, self.attname)
        return getattr(model_instance, self.attname)

    def get_db_prep_save(self, value, connection):
        if isinstance(value, _ClockRead):
            # The door is imported here: the services package imports the models, so the models
            # cannot import it while they are themselves being imported.
            from src.intevia.services.clock import database_now

            moment = database_now(connection.alias)
            setattr(value.instance, value.attname, moment)
            value = moment
        return super().get_db_prep_save(value, connection)
