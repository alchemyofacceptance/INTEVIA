import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_merge_20260609_0555"),
    ]

    operations = [
        migrations.CreateModel(
            name="Contribution",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("contribution_id", models.CharField(max_length=120, unique=True)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("submitted", "Submitted"),
                            ("under_review", "Under review"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                            ("correction_requested", "Correction requested"),
                            (
                                "correction_pending_review",
                                "Correction pending review",
                            ),
                            ("withdrawn", "Withdrawn"),
                            ("archived", "Archived"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=32,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("archived_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "contributor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="contributions",
                        to="core.profile",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ContributionVersion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version_number", models.PositiveIntegerField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("current", "Current"),
                            ("superseded", "Superseded"),
                            ("restricted", "Restricted"),
                            ("erased_content", "Erased content"),
                        ],
                        db_index=True,
                        default="current",
                        max_length=24,
                    ),
                ),
                ("content", models.TextField(blank=True, null=True)),
                ("attachment_references", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("restricted_at", models.DateTimeField(blank=True, null=True)),
                ("erased_at", models.DateTimeField(blank=True, null=True)),
                ("legal_hold", models.BooleanField(default=False)),
                (
                    "contribution",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="versions",
                        to="core.contribution",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_contribution_versions",
                        to="core.profile",
                    ),
                ),
                (
                    "supersedes",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="superseded_by",
                        to="core.contributionversion",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="contribution",
            name="current_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="current_for_contributions",
                to="core.contributionversion",
            ),
        ),
        migrations.CreateModel(
            name="ContributionTransition",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("from_state", models.CharField(max_length=32)),
                ("to_state", models.CharField(max_length=32)),
                ("command", models.CharField(db_index=True, max_length=40)),
                ("authority_reference", models.CharField(max_length=255)),
                ("occurred_at", models.DateTimeField()),
                ("lineage_reference", models.CharField(max_length=255)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="contribution_transitions",
                        to="core.profile",
                    ),
                ),
                (
                    "contribution",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transitions",
                        to="core.contribution",
                    ),
                ),
                (
                    "previous_transition",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="next_transition",
                        to="core.contributiontransition",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transitions",
                        to="core.contributionversion",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ContributionDecision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("decision_type", models.CharField(db_index=True, max_length=32)),
                ("authority_reference", models.CharField(max_length=255)),
                ("rationale_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("decided_at", models.DateTimeField(db_index=True)),
                ("active", models.BooleanField(default=True)),
                (
                    "contribution",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="decisions",
                        to="core.contribution",
                    ),
                ),
                (
                    "decision_actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="contribution_decisions",
                        to="core.profile",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="decisions",
                        to="core.contributionversion",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="EvidenceReference",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("reference", models.CharField(max_length=255)),
                ("reference_type", models.CharField(db_index=True, max_length=40)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "added_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="added_evidence_references",
                        to="core.profile",
                    ),
                ),
                (
                    "contribution",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="evidence_references",
                        to="core.contribution",
                    ),
                ),
                (
                    "decision",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="evidence_references",
                        to="core.contributiondecision",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="evidence_references",
                        to="core.contributionversion",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="contributionversion",
            constraint=models.UniqueConstraint(
                fields=("contribution", "version_number"),
                name="unique_contribution_version_number",
            ),
        ),
        migrations.AddIndex(
            model_name="contributionversion",
            index=models.Index(
                fields=("contribution", "version_number"),
                name="contrib_version_order_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="contributiontransition",
            index=models.Index(
                fields=("contribution", "occurred_at"),
                name="contrib_transition_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="contributiontransition",
            index=models.Index(
                fields=("version", "occurred_at"),
                name="version_transition_time_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="contributiondecision",
            constraint=models.UniqueConstraint(
                condition=Q(active=True),
                fields=("version",),
                name="one_active_decision_per_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="evidencereference",
            constraint=models.UniqueConstraint(
                fields=("contribution", "reference"),
                name="unique_contribution_evidence_reference",
            ),
        ),
        migrations.AddIndex(
            model_name="evidencereference",
            index=models.Index(
                fields=("contribution", "created_at"),
                name="contrib_evidence_time_idx",
            ),
        ),
    ]