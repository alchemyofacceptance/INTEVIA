from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase

from core.models import Identity


class EducationCourseMigrationStructureTests(TransactionTestCase):
    def test_exact_dependency_models_constraints_and_no_sql(self):
        migration = MigrationLoader(connection).get_migration("core", "0018_s014_education_course_foundation")
        self.assertEqual(migration.dependencies, [("core", "0017_s013_profile_effect")])
        from django.db.migrations.operations.models import AddConstraint, AddIndex, CreateModel
        from django.db.migrations.operations.special import RunPython, RunSQL
        self.assertEqual([op.name for op in migration.operations if isinstance(op, CreateModel)], ["Course", "CourseVersion"])
        self.assertFalse(any(isinstance(op, (RunPython, RunSQL, AddIndex)) for op in migration.operations))
        constraints = [
            op.constraint.name
            for op in migration.operations
            if isinstance(op, AddConstraint)
        ]
        self.assertEqual(constraints, [
            "s014_course_id_uniq", "s014_course_current_version_uniq",
            "s014_course_version_number_uniq", "s014_course_actor_action_idem_uniq",
            "s014_course_initial_version_uniq", "s014_course_predecessor_uniq",
            "s014_course_version_lineage_uniq", "s014_course_version_positive",
            "s014_course_action_valid", "s014_course_create_shape",
            "s014_course_append_shape", "s014_course_actor_epoch_nonnegative",
        ])
        state = migration.mutate_state(
            MigrationLoader(connection).project_state(
                [("core", "0017_s013_profile_effect")]
            )
        )
        course = state.apps.get_model("core", "Course")
        version = state.apps.get_model("core", "CourseVersion")
        self.assertEqual(
            [field.name for field in course._meta.fields],
            ["id", "course_id", "created_at", "created_by", "current_version"],
        )
        self.assertEqual(
            [field.name for field in version._meta.fields],
            [
                "id", "version_number", "action", "course_name",
                "course_description", "course_learning_objectives",
                "definition_basis_reference", "actor_access_epoch",
                "authority_reference", "authority_decision_reference",
                "authority_evaluated_at", "request_reference", "idempotency_key",
                "payload_fingerprint", "occurred_at", "lineage_reference",
                "actor", "course", "predecessor",
            ],
        )
        for model, field_name in (
            (course, "created_by"), (course, "current_version"),
            (version, "actor"), (version, "course"), (version, "predecessor"),
        ):
            self.assertFalse(model._meta.get_field(field_name).db_index)


class EducationCourseMigrationRehearsalTests(TransactionTestCase):
    migrate_from = ("core", "0017_s013_profile_effect")
    migrate_to = ("core", "0018_s014_education_course_foundation")

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.leaves = tuple(executor.loader.graph.leaf_nodes())

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.leaves)
        super().tearDown()

    def test_forward_reverse_reapply(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.assertNotIn("core_course", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.assertIn("core_course", connection.introspection.table_names())

    def test_pre_existing_rows_survive_forward_reverse_reapply(self):
        user = User.objects.create_user(username="s014-migration-preserved")
        identity = Identity.objects.create(
            credential=user, access_state=Identity.AccessState.ACTIVE
        )
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(Identity.objects.filter(pk=identity.pk).exists())
        self.assertIn("core_courseversion", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.assertNotIn("core_course", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.assertIn("core_course", connection.introspection.table_names())