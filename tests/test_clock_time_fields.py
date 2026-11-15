"""CLK-10 and CLK-11: the automatic time fields read the INTEVIA Clock.

Thirty-three model fields that Django used to fill from the web server's clock
(``auto_now_add`` and ``auto_now``) are ``core.clock_fields.ClockDateTimeField``. These tests
show that the value written, and the value the caller then holds, is the one the Clock's
Python door gave, on the connection the row was written to; that Django's own rules for when
such a field is filled still hold; and that no model in the product is left on Django's fill.

The clock is not moved here. Where a test must tell the Clock's value from the web server's, it
stands a fixed instant in the door's place and makes the web server's clock unusable.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.apps import apps
from django.db import connection, models
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.clock_fields import ClockDateTimeField, _ClockRead
from core.models import Identity, Role
from src.intevia.services import clock
from src.intevia.services.clock import database_now
from tests.s015_0021_support import seed_identity

FIRST = datetime(2031, 3, 4, 5, 6, 7, 891011, tzinfo=timezone.utc)
FACE_READ = "SELECT public.intevia_clock_instant()"


def no_host_clock():
    raise AssertionError("the web server's clock was read")


class _Door:
    """Stands in the door's place: gives FIRST, then a second later each time, and keeps what it was asked."""

    def __init__(self):
        self.aliases = []

    def __call__(self, database_alias="default", **kwargs):
        self.aliases.append(database_alias)
        return FIRST + timedelta(seconds=len(self.aliases) - 1)


class ClockTimeFieldTests(TestCase):
    def test_automatic_times_are_the_clocks_on_create_save_and_bulk_create(self):
        """CLK-10. A creation stamp and an update stamp are filled from the INTEVIA Clock's door, not the web
        server's clock; the instance holds the value written; and the field is filled exactly when Django
        would fill it."""
        with patch.object(clock, "database_now", _Door()) as door, patch("django.utils.timezone.now", no_host_clock):
            role = Role.objects.create(name="clk-10 created")
            self.assertEqual(role.created_at, FIRST, "CLK-10 create: the instance must hold the Clock's value after the save.")
            self.assertEqual(Role.objects.get(pk=role.pk).created_at, FIRST, "CLK-10 create: the row must hold the Clock's value.")
            self.assertEqual(door.aliases, ["default"], "CLK-10 create: the Clock is read once, on the connection written to.")

            # A later save of the same row: a creation stamp is not filled again.
            role.description = "saved again"
            role.save()
            self.assertEqual((role.created_at, Role.objects.get(pk=role.pk).created_at, len(door.aliases)), (FIRST, FIRST, 1),
                             "CLK-10 save: a creation stamp is filled on the first save only, and the Clock is not read for it again.")

            # bulk_create: one reading for each row, and each instance holds its own.
            many = Role.objects.bulk_create([Role(name="clk-10 bulk a"), Role(name="clk-10 bulk b")])
            stored = dict(Role.objects.filter(name__startswith="clk-10 bulk").values_list("name", "created_at"))
            self.assertEqual([r.created_at for r in many], [FIRST + timedelta(seconds=1), FIRST + timedelta(seconds=2)],
                             "CLK-10 bulk_create: each instance must hold the Clock's value written for it.")
            self.assertEqual(stored, {"clk-10 bulk a": FIRST + timedelta(seconds=1), "clk-10 bulk b": FIRST + timedelta(seconds=2)},
                             "CLK-10 bulk_create: each row must hold the Clock's value.")

            # An update stamp: filled on every save that writes it, and only then. The Identity is made by the
            # tests' shared fixture, with its resolution row, so that the row is one the database accepts.
            with connection.cursor() as cursor:
                identity = Identity.objects.get(pk=seed_identity(cursor, "clk10"))
            born, made = identity.created_at, FIRST + timedelta(seconds=1)
            self.assertEqual(len(door.aliases), 3, "CLK-10: making the fixture by SQL and loading it reads no clock through the door.")
            identity.display_name = "A"
            identity.save()
            self.assertEqual((identity.created_at, identity.updated_at), (born, made + timedelta(seconds=2)),
                             "CLK-10 save: the update stamp moves to the Clock's value; the creation stamp stays.")
            read_so_far = len(door.aliases)
            identity.display_name = "B"
            identity.save(update_fields=["display_name"])
            row = Identity.objects.get(pk=identity.pk)
            self.assertEqual((row.display_name, row.updated_at, len(door.aliases)), ("B", made + timedelta(seconds=2), read_so_far),
                             "CLK-10 update_fields: a save that does not name the update stamp must not fill it or read the Clock.")
            identity.save(update_fields=["display_name", "updated_at"])
            self.assertEqual((identity.updated_at, Identity.objects.get(pk=identity.pk).updated_at),
                             (made + timedelta(seconds=3), made + timedelta(seconds=3)),
                             "CLK-10 update_fields: a save that names the update stamp fills it from the Clock.")
            read_so_far = len(door.aliases)
            Identity.objects.filter(pk=identity.pk).update(display_name="C")
            self.assertEqual((Identity.objects.get(pk=identity.pk).updated_at, len(door.aliases)), (made + timedelta(seconds=3), read_so_far),
                             "CLK-10 QuerySet.update: as in Django, an update that is not a save does not fill the update stamp.")
            self.assertEqual(set(door.aliases), {"default"}, "CLK-10: every reading was on the connection written to.")

        # With nothing stood in the door's place: the reading is one statement to the instant face, and the
        # value lies between two readings of the door taken around the save.
        before = database_now()
        with CaptureQueriesContext(connection) as queries:
            real = Role.objects.create(name="clk-10 real")
        after = database_now()
        self.assertEqual([q["sql"] for q in queries if "intevia_clock" in q["sql"]], [FACE_READ],
                         "CLK-10: a save reads the INTEVIA Clock's instant face once for one automatic field.")
        self.assertTrue(before <= real.created_at <= after and real.created_at == Role.objects.get(pk=real.pk).created_at,
                        "CLK-10: the value written is the Clock's instant at the save, and the instance holds it.")

    def test_every_automatic_time_field_is_the_clocks_and_reads_the_write_connection(self):
        """CLK-11. No model keeps Django's own automatic fill, and the field reads the Clock on the connection it
        is given, whatever that connection is called."""
        automatic, left = [], []
        for model in apps.get_models():
            for field in model._meta.local_fields:
                if isinstance(field, models.DateField) and (field.auto_now or field.auto_now_add):
                    (automatic if type(field) is ClockDateTimeField else left).append("%s.%s" % (model._meta.label, field.name))
        own = sorted(name for name in automatic if name.split(".")[0] in ("core", "email_foundation"))
        self.assertEqual(left, [], "CLK-11: each of these fields is still filled from the web server's clock; make it a "
                                   "core.clock_fields.ClockDateTimeField.")
        self.assertEqual(len(own), 33, "CLK-11: the product's models hold 33 automatic time fields; found %d: %s" % (len(own), own))

        class Elsewhere:
            """The connection in use, under another name: what a second database would look like to the field."""
            alias = "elsewhere"

            def __getattr__(self, name):
                return getattr(connection, name)

        field = Role._meta.get_field("created_at")
        role = Role(name="clk-11 not saved")
        with patch.object(clock, "database_now", _Door()) as door, patch("django.utils.timezone.now", no_host_clock):
            waiting = field.pre_save(role, add=True)
            self.assertIsInstance(waiting, _ClockRead, "CLK-11: the field must not read any clock before it is given a connection.")
            self.assertEqual((role.created_at, door.aliases), (None, []), "CLK-11: nothing is read or set until the write.")
            field.get_db_prep_save(waiting, Elsewhere())
            self.assertEqual((door.aliases, role.created_at), (["elsewhere"], FIRST),
                             "CLK-11: the Clock must be read on the connection the row is written to, and the instance given the value.")
            # A value the caller set on a field that is not being filled is written as given.
            kept = FIRST - timedelta(days=1)
            role.created_at = kept
            self.assertEqual((field.pre_save(role, add=False), door.aliases), (kept, ["elsewhere"]),
                             "CLK-11: a creation stamp on a later save is the instance's own value; no clock is read.")
