"""Guardians for migration 0017_s013_profile_effect."""

from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase
from django.utils import timezone


class S013MigrationStructureTests(TransactionTestCase):
    def _get_migration(self):
        loader = MigrationLoader(connection)
        return loader.get_migration("core", "0017_s013_profile_effect")

    def test_dependency_is_0016_only(self):
        migration = self._get_migration()
        deps = [dep for dep in migration.dependencies if dep[0] == "core"]
        self.assertEqual(deps, [("core", "0016_s012_service_activity_orchestration")])

    def test_no_run_python_or_sql(self):
        from django.db.migrations.operations import RunPython, RunSQL

        migration = self._get_migration()
        for operation in migration.operations:
            self.assertNotIsInstance(operation, RunPython)
            self.assertNotIsInstance(operation, RunSQL)

    def test_operation_order_matches_spec(self):
        from django.db.migrations.operations.models import AddField, CreateModel

        migration = self._get_migration()
        create_model_names = []
        add_field_ops = []
        for index, operation in enumerate(migration.operations):
            if isinstance(operation, CreateModel):
                create_model_names.append((index, operation.name))
            elif isinstance(operation, AddField):
                add_field_ops.append((index, operation.model_name, operation.name))

        root_index = next(index for index, name in create_model_names if name == "ProfileEffectProposalLineage")
        proposal_index = next(index for index, name in create_model_names if name == "ProfileEffectProposalTransition")
        disposition_index = next(index for index, name in create_model_names if name == "ProfileEffectProjectionDisposition")
        self.assertGreater(proposal_index, root_index)
        head_add = next(item for item in add_field_ops if item[1] == "profileeffectproposallineage" and item[2] == "head_proposal_transition")
        self.assertGreater(head_add[0], proposal_index)
        self.assertGreater(disposition_index, head_add[0])

    def test_exact_constraint_and_index_names_are_present(self):
        from django.db.migrations.operations.models import AddConstraint, AddIndex

        migration = self._get_migration()
        constraint_names = set()
        index_names = set()
        for operation in migration.operations:
            if isinstance(operation, AddConstraint):
                constraint_names.add(operation.constraint.name)
            elif isinstance(operation, AddIndex):
                index_names.add(operation.index.name)

        self.assertTrue(
            {
                "s013_pe_lineage_id_uniq",
                "s013_pe_lineage_semantic_uniq",
                "s013_pe_current_survivor_uniq",
                "s013_pe_head_proposal_uniq",
                "s013_pe_subject_proposer_ck",
                "s013_pe_subject_relation_ck",
                "s013_pe_effect_type_ck",
                "s013_pe_contract_version_ck",
                "s013_pe_source_sequence_positive_ck",
                "s013_pe_source_lineage_ref_ck",
                "s013_pe_source_qualification_ref_ck",
                "s013_pe_source_refs_nonempty_ck",
                "s013_pe_prop_sequence_uniq",
                "s013_pe_prop_actor_action_idem_uniq",
                "s013_pe_prop_initial_uniq",
                "s013_pe_prop_successor_uniq",
                "s013_pe_prop_lineage_ref_uniq",
                "s013_pe_prop_sequence_positive_ck",
                "s013_pe_prop_action_valid_ck",
                "s013_pe_prop_from_state_valid_ck",
                "s013_pe_prop_to_state_valid_ck",
                "s013_pe_prop_edge_valid_ck",
                "s013_pe_prop_payload_hex_ck",
                "s013_pe_prop_decision_ref_ck",
                "s013_pe_prop_lineage_ref_ck",
                "s013_pe_prop_refs_nonempty_ck",
                "s013_pe_proj_sequence_uniq",
                "s013_pe_proj_actor_action_idem_uniq",
                "s013_pe_proj_initial_uniq",
                "s013_pe_proj_successor_uniq",
                "s013_pe_proj_lineage_ref_uniq",
                "s013_pe_proj_sequence_positive_ck",
                "s013_pe_proj_action_valid_ck",
                "s013_pe_proj_from_state_valid_ck",
                "s013_pe_proj_to_state_valid_ck",
                "s013_pe_proj_edge_valid_ck",
                "s013_pe_proj_payload_hex_ck",
                "s013_pe_proj_decision_ref_ck",
                "s013_pe_proj_lineage_ref_ck",
                "s013_pe_proj_refs_nonempty_ck",
            }.issubset(constraint_names)
        )
        self.assertEqual(
            index_names,
            {
                "s013_pe_subject_idx",
                "s013_pe_source_activity_idx",
                "s013_pe_source_lineage_idx",
                "s013_pe_prop_actor_idx",
                "s013_pe_prop_root_action_idx",
                "s013_pe_proj_actor_idx",
                "s013_pe_proj_prop_action_idx",
            },
        )


class S013MigrationRehearsalTests(TransactionTestCase):
    migrate_from = ("core", "0016_s012_service_activity_orchestration")
    migrate_to = ("core", "0017_s013_profile_effect")

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.received_leaf_targets = tuple(executor.loader.graph.leaf_nodes())
        apps = self.migrate(self.migrate_from)
        User = apps.get_model("auth", "User")
        Identity = apps.get_model("core", "Identity")
        Service = apps.get_model("core", "Service")
        ServiceVersion = apps.get_model("core", "ServiceVersion")
        ServiceActivity = apps.get_model("core", "ServiceActivity")
        ServiceActivityTransition = apps.get_model("core", "ServiceActivityTransition")
        ServiceActivityEvidenceReference = apps.get_model("core", "ServiceActivityEvidenceReference")

        occurred_at = timezone.now()
        credential = User.objects.create(username="s013-migration", password="unused")
        identity = Identity.objects.create(credential=credential, access_state="active")
        service = Service.objects.create(
            service_id="migration:s013:service",
            state="published",
            created_by=identity,
            created_at=occurred_at,
        )
        version = ServiceVersion.objects.create(
            service=service,
            version_number=1,
            capability_purpose="Migration rehearsal",
            domain_intent="Migration rehearsal",
            created_by=identity,
            created_at=occurred_at,
        )
        service.current_version = version
        service.save()
        activity = ServiceActivity.objects.create(
            activity_id=uuid4(),
            service_version=version,
            initiating_domain="service",
            initiating_domain_reference="MIGRATE-S013-001",
            state="unassigned",
            created_by=identity,
            created_at=occurred_at,
        )
        transition = ServiceActivityTransition.objects.create(
            activity=activity,
            sequence=1,
            previous_transition=None,
            action="CREATE",
            from_state=None,
            to_state="unassigned",
            actor=identity,
            actor_access_epoch=0,
            authority_reference="AUTH-S012-MIGRATION",
            authority_decision_reference="s012d1:" + "a" * 64,
            authority_evaluated_at=occurred_at,
            request_reference="REQ-S012-MIGRATION",
            idempotency_key="IDEM-S012-MIGRATION",
            payload_fingerprint="b" * 64,
            occurred_at=occurred_at,
            lineage_reference="s012l1:" + "c" * 64,
        )
        activity.head_transition = transition
        activity.save()
        ServiceActivityEvidenceReference.objects.create(
            transition=transition,
            evidence_kind="activity_basis",
            reference="EVIDENCE-S012-MIGRATION",
            supplied_by=identity,
            authority_reference="AUTH-S012-EVIDENCE",
            occurred_at=occurred_at,
        )
        self.activity_pk = activity.pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.received_leaf_targets)
        super().tearDown()

    def test_forward_reverse_reapply_preserves_s012_rows(self):
        apps = self.migrate(self.migrate_to)
        ServiceActivity = apps.get_model("core", "ServiceActivity")
        self.assertTrue(ServiceActivity.objects.filter(pk=self.activity_pk).exists())
        self.assertIn("core_profileeffectproposallineage", connection.introspection.table_names())
        apps = self.migrate(self.migrate_from)
        ServiceActivity = apps.get_model("core", "ServiceActivity")
        self.assertTrue(ServiceActivity.objects.filter(pk=self.activity_pk).exists())
        self.assertNotIn("core_profileeffectproposallineage", connection.introspection.table_names())
        apps = self.migrate(self.migrate_to)
        ServiceActivity = apps.get_model("core", "ServiceActivity")
        self.assertTrue(ServiceActivity.objects.filter(pk=self.activity_pk).exists())