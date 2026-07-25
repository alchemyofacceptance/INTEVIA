import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_s008_event_attendance_foundation"),
    ]

    operations = [
        migrations.CreateModel(
            name="EventResourceRelationship",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("relationship_id", models.CharField(max_length=120, unique=True)),
                ("created_at", models.DateTimeField()),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="resource_relationships", to="core.event")),
                ("library_resource", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="event_relationships", to="core.libraryresource")),
            ],
            options={
                "indexes": [models.Index(fields=["event", "created_at"], name="event_resource_lineage_idx")],
                "constraints": [models.UniqueConstraint(fields=("event", "library_resource"), name="unique_event_resource_lineage")],
            },
        ),
        migrations.CreateModel(
            name="EventResourceAssertion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveIntegerField()),
                ("purpose", models.CharField(choices=[("PREPARATION", "Preparation"), ("DURING_EVENT", "During the Event"), ("FOLLOW_UP", "Follow-up"), ("REFERENCE", "Reference")], max_length=24)),
                ("state", models.CharField(choices=[("CURRENT", "Current"), ("RETIRED", "Retired"), ("SUPERSEDED", "Superseded"), ("VOIDED", "Voided")], max_length=16)),
                ("actor_access_epoch", models.PositiveBigIntegerField()),
                ("created_at", models.DateTimeField()),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_event_resource_assertions", to="core.identity")),
                ("library_resource_version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="event_resource_assertions", to="core.libraryresourceversion")),
                ("predecessor", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="successor", to="core.eventresourceassertion")),
                ("relationship", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="assertions", to="core.eventresourcerelationship")),
            ],
            options={
                "indexes": [models.Index(fields=["relationship", "revision"], name="event_resource_assertion_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("relationship", "revision"), name="unique_event_resource_revision"),
                    models.UniqueConstraint(condition=models.Q(("predecessor__isnull", True)), fields=("relationship",), name="one_initial_event_resource_assertion"),
                    models.CheckConstraint(condition=models.Q(("revision__gte", 1)), name="event_resource_revision_positive"),
                ],
            },
        ),
        migrations.AddField(
            model_name="eventresourcerelationship",
            name="head_assertion",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="headed_relationship", to="core.eventresourceassertion"),
        ),
        migrations.CreateModel(
            name="EventResourceRelationshipTransition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sequence", models.PositiveIntegerField()),
                ("action", models.CharField(choices=[("CREATE", "Create"), ("SUPERSEDE_VERSION", "Supersede version"), ("AMEND_PURPOSE", "Amend purpose"), ("RETIRE", "Retire"), ("VOID", "Void")], db_index=True, max_length=24)),
                ("prior_disposition", models.CharField(blank=True, choices=[("CURRENT", "Current"), ("RETIRED", "Retired"), ("SUPERSEDED", "Superseded"), ("VOIDED", "Voided")], max_length=16, null=True)),
                ("actor_access_epoch", models.PositiveBigIntegerField()),
                ("authority_scope", models.CharField(max_length=64)),
                ("event_authority_reference", models.CharField(db_index=True, max_length=255)),
                ("event_authority_evaluated_at", models.DateTimeField()),
                ("request_reference", models.CharField(db_index=True, max_length=128)),
                ("consumer_reference", models.CharField(max_length=128)),
                ("idempotency_key", models.CharField(max_length=120)),
                ("payload_fingerprint", models.CharField(max_length=64)),
                ("transaction_reference", models.UUIDField(unique=True)),
                ("occurred_at", models.DateTimeField()),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="event_resource_relationship_transitions", to="core.identity")),
                ("from_assertion", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="outgoing_transition", to="core.eventresourceassertion")),
                ("previous_transition", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="next_transition", to="core.eventresourcerelationshiptransition")),
                ("relationship", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transitions", to="core.eventresourcerelationship")),
                ("resulting_assertion", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="resulting_transition", to="core.eventresourceassertion")),
            ],
            options={
                "indexes": [models.Index(fields=["relationship", "occurred_at"], name="event_resource_transition_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("relationship", "sequence"), name="unique_event_resource_sequence"),
                    models.UniqueConstraint(condition=models.Q(("previous_transition__isnull", True)), fields=("relationship",), name="one_initial_event_resource_transition"),
                    models.UniqueConstraint(fields=("actor", "action", "idempotency_key"), name="unique_event_resource_idempotency"),
                    models.CheckConstraint(condition=models.Q(("sequence__gte", 1)), name="event_resource_sequence_positive"),
                ],
            },
        ),
        migrations.CreateModel(
            name="EventResourceRelationshipEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("EVENT_AUTHORITY", "Event authority"), ("LIBRARY_AUTHORITY", "Library authority"), ("LIBRARY_LINKABILITY", "Library linkability"), ("LIBRARY_DISCLOSURE_ELIGIBILITY", "Library disclosure eligibility"), ("RELATIONSHIP_DISCLOSURE_ELIGIBILITY", "Relationship disclosure eligibility"), ("CORRECTION", "Correction")], max_length=48)),
                ("schema_id", models.CharField(max_length=120)),
                ("schema_version", models.PositiveIntegerField()),
                ("canonicalization", models.CharField(max_length=120)),
                ("result", models.CharField(max_length=48)),
                ("determination_reference", models.CharField(db_index=True, max_length=255)),
                ("policy_reference", models.CharField(db_index=True, max_length=255)),
                ("authority_binding_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("provider_snapshot_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("canonical_payload", models.BinaryField()),
                ("payload_sha256", models.CharField(db_index=True, max_length=64)),
                ("actor_identity_id", models.UUIDField(blank=True, null=True)),
                ("actor_access_epoch", models.PositiveBigIntegerField(blank=True, null=True)),
                ("viewer_identity_id", models.UUIDField(blank=True, null=True)),
                ("viewer_access_epoch", models.PositiveBigIntegerField(blank=True, null=True)),
                ("request_reference", models.CharField(blank=True, max_length=128, null=True)),
                ("consumer_reference", models.CharField(blank=True, max_length=128, null=True)),
                ("evaluated_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("transition", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="evidence", to="core.eventresourcerelationshiptransition")),
            ],
            options={
                "indexes": [models.Index(fields=["transition", "kind", "evaluated_at"], name="event_resource_evidence_idx")],
                "constraints": [models.UniqueConstraint(fields=("transition", "kind"), name="unique_event_resource_evidence_kind")],
            },
        ),
    ]