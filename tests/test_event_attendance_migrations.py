from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class EventAttendanceMigrationTests(TransactionTestCase):
    migrate_from = ("core", "0013_s007_identity_constraints")
    migrate_to = ("core", "0014_s008_event_attendance_foundation")

    @staticmethod
    def migrate(target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def setUp(self):
        old_apps = self.migrate(self.migrate_from)
        User = old_apps.get_model("auth", "User")
        Identity = old_apps.get_model("core", "Identity")
        Event = old_apps.get_model("core", "Event")
        EventParticipation = old_apps.get_model("core", "EventParticipation")
        EventRegistration = old_apps.get_model("core", "EventRegistration")

        credential = User.objects.create(username="s008-migration-subject")
        identity = Identity.objects.create(
            credential=credential,
            identity_id="00000000-0000-0000-0000-000000000008",
            canonical_username="s008-migration-subject",
            access_state="active",
        )
        occurred_at = timezone.now()
        event = Event.objects.create(
            event_id="event:s008:migration",
            title="S008 migration",
            owner=identity,
            state="active",
            created_at=occurred_at,
        )
        participation = EventParticipation.objects.create(
            event=event,
            participant=identity,
            attached_by=identity,
            authority_reference="authority:historical-participation",
            attached_at=occurred_at,
        )
        registration = EventRegistration.objects.create(
            registration_id="registration:s008:migration",
            event=event,
            participant=identity,
            state="registered",
            origin="self",
            event_state_at_registration="active",
            eligibility_basis_type="other",
            eligibility_basis_reference="eligibility:migration",
            eligibility_evaluated_at=occurred_at,
            registered_at=occurred_at,
        )
        self.identity_pk = identity.pk
        self.event_pk = event.pk
        self.participation_pk = participation.pk
        self.registration_pk = registration.pk

    def tearDown(self):
        self.migrate(self.migrate_to)
        super().tearDown()

    def assert_legacy_rows(self, apps):
        self.assertTrue(
            apps.get_model("core", "Identity").objects.filter(
                pk=self.identity_pk
            ).exists()
        )
        self.assertTrue(
            apps.get_model("core", "Event").objects.filter(
                pk=self.event_pk
            ).exists()
        )
        self.assertTrue(
            apps.get_model("core", "EventParticipation").objects.filter(
                pk=self.participation_pk
            ).exists()
        )
        self.assertTrue(
            apps.get_model("core", "EventRegistration").objects.filter(
                pk=self.registration_pk
            ).exists()
        )

    def test_schema_only_forward_reverse_and_reapply_preserve_history(self):
        new_apps = self.migrate(self.migrate_to)
        self.assert_legacy_rows(new_apps)
        self.assertEqual(
            new_apps.get_model("core", "EventAttendance").objects.count(),
            0,
        )

        old_apps = self.migrate(self.migrate_from)
        self.assert_legacy_rows(old_apps)
        self.assertNotIn(
            "core_eventattendance",
            connection.introspection.table_names(),
        )

        reapplied_apps = self.migrate(self.migrate_to)
        self.assert_legacy_rows(reapplied_apps)
        self.assertEqual(
            reapplied_apps.get_model("core", "EventAttendance").objects.count(),
            0,
        )
