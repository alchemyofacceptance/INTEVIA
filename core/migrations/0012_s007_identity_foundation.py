import unicodedata
import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


MAX_CANONICAL_USERNAME_LENGTH = 255


def canonical_username_v1(username):
    canonical = unicodedata.normalize("NFKC", username).casefold().strip()
    if not canonical:
        raise RuntimeError("username is empty after normalisation")
    if any(character.isspace() for character in canonical):
        raise RuntimeError("username contains internal whitespace")
    if any(
        unicodedata.category(character) in {"Cc", "Cf"}
        for character in canonical
    ):
        raise RuntimeError("username contains control or format characters")
    if len(canonical) > MAX_CANONICAL_USERNAME_LENGTH:
        raise RuntimeError("canonical username is too long")
    return canonical


def backfill_identity_values(apps, schema_editor):
    Identity = apps.get_model("core", "Identity")
    seen = {}
    for identity in Identity.objects.select_related("credential").order_by("pk"):
        canonical = canonical_username_v1(identity.credential.username)
        if canonical in seen:
            raise RuntimeError(
                "canonical username collision between Identity rows "
                f"{seen[canonical]} and {identity.pk}"
            )
        seen[canonical] = identity.pk
        identity.identity_id = uuid.uuid4()
        identity.canonical_username = canonical
        identity.access_state = "pending"
        identity.access_epoch = 0
        identity.save(
            update_fields=(
                "identity_id",
                "canonical_username",
                "access_state",
                "access_epoch",
            )
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0011_s007_identity_rename")]

    operations = [
        migrations.AddField(
            model_name="identity",
            name="identity_id",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name="identity",
            name="canonical_username",
            field=models.CharField(max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="identity",
            name="access_state",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("active", "Active"),
                    ("restricted", "Restricted"),
                    ("deactivated", "Deactivated"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="identity",
            name="access_epoch",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="identity",
            name="updated_at",
            field=models.DateTimeField(default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="identity",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="identity",
            name="restricted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="identity",
            name="deactivated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_identity_values, migrations.RunPython.noop),
        migrations.CreateModel(
            name="OriginatingMembershipProvisioningRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("organism_reference", models.CharField(max_length=120)),
                ("contract_version", models.PositiveIntegerField()),
                ("correlation_id", models.UUIDField(unique=True)),
                ("authority_reference", models.CharField(max_length=255)),
                ("evidence_reference", models.CharField(max_length=255)),
                ("requested_at", models.DateTimeField()),
                ("superseded_at", models.DateTimeField(blank=True, null=True)),
                ("identity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="originating_membership_requests", to="core.identity")),
                ("supersedes", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="superseded_by", to="core.originatingmembershipprovisioningrequest")),
            ],
            options={
                "indexes": [models.Index(fields=["identity", "requested_at"], name="originating_request_time_idx")],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("superseded_at__isnull", True)), fields=("identity", "organism_reference"), name="one_active_originating_membership_intent"),
                    models.CheckConstraint(condition=models.Q(("contract_version__gte", 1)), name="originating_contract_version_positive"),
                    models.CheckConstraint(condition=models.Q(("supersedes__isnull", True), models.Q(("pk", models.F("supersedes_id")), _negated=True), _connector="OR"), name="originating_request_not_self_superseding"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IdentityTransition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("provision", "Provision"), ("activate", "Activate"), ("restrict", "Restrict"), ("deactivate", "Deactivate"), ("reactivate", "Reactivate"), ("replace_credential", "Replace credential"), ("retire_credential", "Retire credential"), ("update_username", "Update username"), ("correct", "Correct")], max_length=32)),
                ("prior_state", models.CharField(blank=True, choices=[("pending", "Pending"), ("active", "Active"), ("restricted", "Restricted"), ("deactivated", "Deactivated")], max_length=16, null=True)),
                ("resulting_state", models.CharField(choices=[("pending", "Pending"), ("active", "Active"), ("restricted", "Restricted"), ("deactivated", "Deactivated")], max_length=16)),
                ("authority_reference", models.CharField(max_length=255)),
                ("technical_executor", models.CharField(max_length=120)),
                ("evidence_reference", models.CharField(max_length=255)),
                ("reason_category", models.CharField(choices=[("provisioning", "Provisioning"), ("governed_activation", "Governed activation"), ("access_review", "Access review"), ("security", "Security"), ("human_request", "Human request"), ("credential_change", "Credential change"), ("correction", "Correction"), ("other", "Other")], max_length=32)),
                ("correlation_id", models.UUIDField(unique=True)),
                ("occurred_at", models.DateTimeField()),
                ("corrects_transition", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="corrections", to="core.identitytransition")),
                ("identity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="access_transitions", to="core.identity")),
                ("previous_transition", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="next_transition", to="core.identitytransition")),
                ("requesting_actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="requested_identity_transitions", to="core.identity")),
            ],
            options={
                "indexes": [models.Index(fields=["identity", "occurred_at"], name="identity_transition_time_idx")],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("action", "provision"), ("prior_state__isnull", False), _connector="OR"), name="identity_transition_prior_required"),
                    models.CheckConstraint(condition=models.Q(("corrects_transition__isnull", True), ("action", "correct"), _connector="OR"), name="identity_correction_action_required"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ProvisioningReconciliationAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("state", models.CharField(choices=[("requested", "Requested"), ("dispatched", "Dispatched"), ("acknowledged", "Acknowledged"), ("fulfilled", "Fulfilled"), ("held", "Held"), ("rejected", "Rejected"), ("superseded", "Superseded"), ("corrected", "Corrected")], max_length=16)),
                ("authority_reference", models.CharField(max_length=255)),
                ("evidence_reference", models.CharField(max_length=255)),
                ("correlation_id", models.UUIDField(unique=True)),
                ("occurred_at", models.DateTimeField()),
                ("corrects_attempt", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="corrections", to="core.provisioningreconciliationattempt")),
                ("previous_attempt", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="next_attempt", to="core.provisioningreconciliationattempt")),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reconciliation_attempts", to="core.originatingmembershipprovisioningrequest")),
            ],
            options={
                "indexes": [models.Index(fields=["request", "occurred_at"], name="provision_attempt_time_idx")],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("corrects_attempt__isnull", True), ("state", "corrected"), _connector="OR"), name="provision_correction_state_required"),
                    models.CheckConstraint(condition=models.Q(("previous_attempt__isnull", True), models.Q(("pk", models.F("previous_attempt_id")), _negated=True), _connector="OR"), name="provision_attempt_not_self_previous"),
                ],
            },
        ),
    ]