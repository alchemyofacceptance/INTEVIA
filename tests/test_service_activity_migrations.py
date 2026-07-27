"""Guardians for S012 migration 0016: operation order, dependencies, constraint catalogue, and rehearsal."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase
from django.utils import timezone


class S012MigrationStructureTests(TransactionTestCase):
    """Validate migration 0016 structure without live application."""

    def _get_migration(self):
        loader = MigrationLoader(connection)
        return loader.get_migration("core", "0016_s012_service_activity_orchestration")

    def test_dependency_is_predecessor_only(self):
        migration = self._get_migration()
        deps = [d for d in migration.dependencies if d[0] == "core"]
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0], ("core", "0015_s011b_event_resource_relationship"))

    def test_no_run_python_operations(self):
        from django.db.migrations.operations import RunPython, RunSQL
        migration = self._get_migration()
        for op in migration.operations:
            self.assertNotIsInstance(op, RunPython,
                                    "migration must not contain RunPython")
            self.assertNotIsInstance(op, RunSQL,
                                    "migration must not contain RunSQL")

    def test_operation_order_matches_spec(self):
        """§5.7: Create Activity (no head), Create Transition, Add head, then children."""
        migration = self._get_migration()
        from django.db.migrations.operations.models import CreateModel, AddField

        create_model_names = []
        add_field_ops = []
        for i, op in enumerate(migration.operations):
            if isinstance(op, CreateModel):
                create_model_names.append((i, op.name))
            elif isinstance(op, AddField):
                add_field_ops.append((i, op.model_name, op.name))

        # Step 1: ServiceActivity created first
        sa_idx = next(i for i, n in create_model_names if n == "ServiceActivity")
        # Step 2: Transition created second (before head_transition is added)
        st_idx = next(i for i, n in create_model_names if n == "ServiceActivityTransition")
        self.assertGreater(st_idx, sa_idx)

        # Step 3: head_transition added after Transition model exists
        head_add = next(
            (i, m, f) for i, m, f in add_field_ops
            if m.lower() == "serviceactivity" and f == "head_transition"
        )
        self.assertGreater(head_add[0], st_idx)

        # Step 4: Assignment, WorkSubmission, Review, Evidence all after head
        child_names = {
            "ServiceActivityAssignment",
            "ServiceWorkSubmission",
            "ServiceActivityReview",
            "ServiceActivityEvidenceReference",
        }
        for idx, name in create_model_names:
            if name in child_names:
                self.assertGreater(idx, head_add[0],
                                   f"{name} must be created after head_transition")

    def test_exact_constraint_and_index_names_in_operations(self):
        """Every §5.7 name appears in the migration operations."""
        migration = self._get_migration()
        from django.db.migrations.operations.models import AddConstraint, AddIndex

        constraint_names = set()
        index_names = set()
        for op in migration.operations:
            if isinstance(op, AddConstraint):
                constraint_names.add(op.constraint.name)
            elif isinstance(op, AddIndex):
                index_names.add(op.index.name)

        expected_constraints = {
            "s012_activity_id_uniq",
            "s012_activity_head_uniq",
            "s012_activity_domain_valid_ck",
            "s012_activity_state_valid_ck",
            "s012_activity_refs_nonempty_ck",
            "s012_assignment_activity_uniq",
            "s012_assignment_transition_uniq",
            "s012_assignment_refs_nonempty_ck",
            "s012_submission_activity_uniq",
            "s012_submission_transition_uniq",
            "s012_submission_refs_nonempty_ck",
            "s012_review_submission_uniq",
            "s012_review_transition_uniq",
            "s012_review_refs_nonempty_ck",
            "s012_transition_activity_sequence_uniq",
            "s012_activity_actor_action_idem_uniq",
            "s012_transition_initial_uniq",
            "s012_transition_successor_uniq",
            "s012_transition_lineage_ref_uniq",
            "s012_transition_sequence_positive_ck",
            "s012_transition_action_valid_ck",
            "s012_transition_from_state_valid_ck",
            "s012_transition_to_state_valid_ck",
            "s012_transition_edge_valid_ck",
            "s012_transition_payload_hex_ck",
            "s012_transition_decision_ref_ck",
            "s012_transition_lineage_ref_ck",
            "s012_transition_refs_nonempty_ck",
            "s012_evidence_tuple_uniq",
            "s012_evidence_kind_valid_ck",
            "s012_evidence_refs_nonempty_ck",
        }
        self.assertEqual(constraint_names & expected_constraints, expected_constraints)

        expected_indexes = {
            "s012_activity_service_version_idx",
            "s012_activity_state_idx",
            "s012_activity_created_by_idx",
            "s012_assignment_assignee_idx",
            "s012_assignment_assigned_by_idx",
            "s012_submission_submitted_by_idx",
            "s012_review_reviewed_by_idx",
            "s012_transition_actor_idx",
            "s012_transition_activity_action_idx",
            "s012_evidence_supplied_by_idx",
        }
        self.assertEqual(index_names & expected_indexes, expected_indexes)

    def test_no_governance_directory_touched(self):
        """The migration file must not reference core/migrations/governance/."""
        import inspect
        migration = self._get_migration()
        source = inspect.getsource(type(migration))
        self.assertNotIn("governance", source)

    def test_identifier_lengths_within_63_chars(self):
        """All S012 constraint and index names fit in 63 characters."""
        migration = self._get_migration()
        from django.db.migrations.operations.models import AddConstraint, AddIndex

        for op in migration.operations:
            if isinstance(op, AddConstraint) and op.constraint.name.startswith("s012_"):
                self.assertLessEqual(
                    len(op.constraint.name), 63,
                    f"constraint name too long: {op.constraint.name}"
                )
            elif isinstance(op, AddIndex) and op.index.name.startswith("s012_"):
                self.assertLessEqual(
                    len(op.index.name), 63,
                    f"index name too long: {op.index.name}"
                )


class S012MigrationRehearsalTests(TransactionTestCase):
    """Forward -> reverse -> reapply rehearsal on SQLite with pre-existing data."""

    migrate_from = ("core", "0015_s011b_event_resource_relationship")
    migrate_to = ("core", "0016_s012_service_activity_orchestration")

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.received_leaf_targets = tuple(executor.loader.graph.leaf_nodes())
        old_apps = self.migrate(self.migrate_from)

        User = old_apps.get_model("auth", "User")
        Identity = old_apps.get_model("core", "Identity")
        Service = old_apps.get_model("core", "Service")
        ServiceVersion = old_apps.get_model("core", "ServiceVersion")
        Event = old_apps.get_model("core", "Event")

        occurred_at = timezone.now()
        credential = User.objects.create(
            username="MigrationS012",
            password="unusable-placeholder",
        )
        self.identity = Identity.objects.create(
            credential=credential,
            access_state="active",
        )
        service = Service.objects.create(
            service_id="migration:s012:service",
            state="published",
            created_by=self.identity,
            created_at=occurred_at,
        )
        version = ServiceVersion.objects.create(
            service=service,
            version_number=1,
            capability_purpose="Migration test",
            domain_intent="Testing",
            created_by=self.identity,
            created_at=occurred_at,
        )
        service.current_version = version
        service.save()

        event = Event.objects.create(
            event_id="migration:s012:event",
            title="Migration Event",
            owner=self.identity,
            state="published",
            created_at=occurred_at,
        )

        self.identity_pk = self.identity.pk
        self.service_pk = service.pk
        self.version_pk = version.pk
        self.event_pk = event.pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.received_leaf_targets)
        super().tearDown()

    def _assert_pre_existing_preserved(self, apps):
        Identity = apps.get_model("core", "Identity")
        Service = apps.get_model("core", "Service")
        ServiceVersion = apps.get_model("core", "ServiceVersion")
        Event = apps.get_model("core", "Event")

        self.assertTrue(Identity.objects.filter(pk=self.identity_pk).exists())
        self.assertTrue(Service.objects.filter(pk=self.service_pk).exists())
        self.assertTrue(ServiceVersion.objects.filter(pk=self.version_pk).exists())
        self.assertTrue(Event.objects.filter(pk=self.event_pk).exists())

    def test_forward_reverse_reapply_preserves_data(self):
        # Forward
        new_apps = self.migrate(self.migrate_to)
        self._assert_pre_existing_preserved(new_apps)

        # Verify S012 tables exist
        tables = connection.introspection.table_names()
        for table in [
            "core_serviceactivity",
            "core_serviceactivitytransition",
            "core_serviceactivityassignment",
            "core_serviceworksubmission",
            "core_serviceactivityreview",
            "core_serviceactivityevidencereference",
        ]:
            self.assertIn(table, tables)

        # Reverse
        old_apps = self.migrate(self.migrate_from)
        self._assert_pre_existing_preserved(old_apps)

        tables_after_reverse = connection.introspection.table_names()
        self.assertNotIn("core_serviceactivity", tables_after_reverse)

        # Reapply
        reapplied_apps = self.migrate(self.migrate_to)
        self._assert_pre_existing_preserved(reapplied_apps)

        tables_reapplied = connection.introspection.table_names()
        self.assertIn("core_serviceactivity", tables_reapplied)

    def test_leaf_state_restored_after_rehearsal(self):
        self.migrate(self.migrate_to)
        self.migrate(self.migrate_from)
        self.migrate(self.migrate_to)
        executor = MigrationExecutor(connection)
        executor.migrate(self.received_leaf_targets)
        final_tables = connection.introspection.table_names()
        self.assertIn("core_serviceactivity", final_tables)


class IdentityFKCatalogueTests(TransactionTestCase):
    """Pre-authored exact Identity FK catalogue: 34 base + 7 S012 additions = 41."""

    # Base 34 from the existing S007 PostgreSQL catalogue. Identity-internal
    # and originating-provisioning relations are deliberately out of scope.
    BASE_IDENTITY_FKS = {
        # ProfileRole
        ("profilerole", "identity"),
        # Contribution
        ("contribution", "contributor"),
        # ContributionVersion
        ("contributionversion", "created_by"),
        # ContributionTransition
        ("contributiontransition", "actor"),
        # ContributionDecision
        ("contributiondecision", "decision_actor"),
        # EvidenceReference (Contribution)
        ("evidencereference", "added_by"),
        # Event
        ("event", "owner"),
        # EventTransition
        ("eventtransition", "actor"),
        # EventParticipation
        ("eventparticipation", "participant"),
        ("eventparticipation", "attached_by"),
        # EventEvidenceReference
        ("eventevidencereference", "supplied_by"),
        # EventRegistration
        ("eventregistration", "participant"),
        # EventRegistrationTransition
        ("eventregistrationtransition", "actor"),
        ("eventregistrationtransition", "authority_participant"),
        # EventRegistrationEvidenceReference
        ("eventregistrationevidencereference", "supplied_by"),
        # EventAttendance
        ("eventattendance", "subject"),
        # EventAttendanceTransition
        ("eventattendancetransition", "actor"),
        # EventAttendanceEvidenceReference
        ("eventattendanceevidencereference", "supplied_by"),
        # EventAttendanceEligibilityReceipt
        ("eventattendanceeligibilityreceipt", "actor"),
        ("eventattendanceeligibilityreceipt", "subject"),
        # EventResourceAssertion
        ("eventresourceassertion", "created_by"),
        # EventResourceRelationshipTransition
        ("eventresourcerelationshiptransition", "actor"),
        # LibraryResource
        ("libraryresource", "created_by"),
        # LibraryResourceVersion
        ("libraryresourceversion", "created_by"),
        # LibraryResourceTransition
        ("libraryresourcetransition", "actor"),
        # LibraryResourceEvidenceReference
        ("libraryresourceevidencereference", "supplied_by"),
        # Service
        ("service", "created_by"),
        # ServiceVersion
        ("serviceversion", "created_by"),
        # ServiceTransition
        ("servicetransition", "actor"),
        # ServiceEvidenceReference
        ("serviceevidencereference", "supplied_by"),
        # LibraryServiceAssociation
        ("libraryserviceassociation", "actor"),
        # ServiceEventAssociation
        ("serviceeventassociation", "actor"),
        # ServiceDeliveryEvidenceReference
        ("servicedeliveryevidencereference", "supplied_by"),
        # CareResponse
        ("careresponse", "actor"),
    }

    S012_ADDITIONS = {
        ("serviceactivity", "created_by"),
        ("serviceactivitytransition", "actor"),
        ("serviceactivityassignment", "assignee"),
        ("serviceactivityassignment", "assigned_by"),
        ("serviceworksubmission", "submitted_by"),
        ("serviceactivityreview", "reviewed_by"),
        ("serviceactivityevidencereference", "supplied_by"),
    }

    def test_catalogue_34_base_plus_7_additions_equals_41(self):
        self.assertEqual(len(self.BASE_IDENTITY_FKS), 34)
        self.assertEqual(len(self.S012_ADDITIONS), 7)
        combined = self.BASE_IDENTITY_FKS | self.S012_ADDITIONS
        self.assertEqual(len(combined), 41)

    def test_live_schema_matches_catalogue(self):
        from django.apps import apps
        identity_model = apps.get_model("core", "Identity")
        live_fks = set()
        for model in apps.get_models():
            if model._meta.app_label != "core":
                continue
            if model._meta.model_name in {
                "identitytransition",
                "originatingmembershipprovisioningrequest",
            }:
                continue
            for field in model._meta.get_fields():
                if (
                    hasattr(field, "related_model")
                    and field.related_model is identity_model
                    and hasattr(field, "column")
                ):
                    live_fks.add((model._meta.model_name, field.name))

        expected = self.BASE_IDENTITY_FKS | self.S012_ADDITIONS
        self.assertEqual(live_fks, expected)
        self.assertEqual(len(live_fks), 41)
