import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_s013_profile_effect"),
    ]

    operations = [
        migrations.CreateModel(
            name="Course",
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
                ("course_id", models.UUIDField(editable=False)),
                ("created_at", models.DateTimeField()),
                (
                    "created_by",
                    models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_courses",
                        to="core.identity",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CourseVersion",
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
                    "action",
                    models.CharField(
                        choices=[
                            ("CREATE", "Create"),
                            ("APPEND_VERSION", "Append version"),
                        ],
                        max_length=14,
                    ),
                ),
                ("course_name", models.CharField(max_length=45)),
                ("course_description", models.TextField()),
                ("course_learning_objectives", models.TextField()),
                ("definition_basis_reference", models.CharField(max_length=255)),
                ("actor_access_epoch", models.PositiveBigIntegerField()),
                ("authority_reference", models.CharField(max_length=255)),
                ("authority_decision_reference", models.CharField(max_length=71)),
                ("authority_evaluated_at", models.DateTimeField()),
                ("request_reference", models.CharField(max_length=128)),
                ("idempotency_key", models.CharField(max_length=120)),
                ("payload_fingerprint", models.CharField(max_length=64)),
                ("occurred_at", models.DateTimeField()),
                ("lineage_reference", models.CharField(max_length=71)),
                (
                    "actor",
                    models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="authored_course_versions",
                        to="core.identity",
                    ),
                ),
                (
                    "course",
                    models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="versions",
                        to="core.course",
                    ),
                ),
                (
                    "predecessor",
                    models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="successors",
                        to="core.courseversion",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="course",
            name="current_version",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="current_for_courses",
                to="core.courseversion",
            ),
        ),
        migrations.AddConstraint(
            model_name="course",
            constraint=models.UniqueConstraint(
                fields=("course_id",), name="s014_course_id_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="course",
            constraint=models.UniqueConstraint(
                condition=models.Q(current_version__isnull=False),
                fields=("current_version",),
                name="s014_course_current_version_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.UniqueConstraint(
                fields=("course", "version_number"),
                name="s014_course_version_number_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.UniqueConstraint(
                fields=("actor", "action", "idempotency_key"),
                name="s014_course_actor_action_idem_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(predecessor__isnull=True),
                fields=("course",),
                name="s014_course_initial_version_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.UniqueConstraint(
                condition=models.Q(predecessor__isnull=False),
                fields=("predecessor",),
                name="s014_course_predecessor_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.UniqueConstraint(
                fields=("lineage_reference",),
                name="s014_course_version_lineage_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.CheckConstraint(
                condition=models.Q(version_number__gte=1),
                name="s014_course_version_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.CheckConstraint(
                condition=models.Q(action__in=("CREATE", "APPEND_VERSION")),
                name="s014_course_action_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        action="CREATE", predecessor__isnull=True, version_number=1
                    ),
                    ~models.Q(action="CREATE"),
                    _connector="OR",
                ),
                name="s014_course_create_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        action="APPEND_VERSION",
                        predecessor__isnull=False,
                        version_number__gte=2,
                    ),
                    ~models.Q(action="APPEND_VERSION"),
                    _connector="OR",
                ),
                name="s014_course_append_shape",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseversion",
            constraint=models.CheckConstraint(
                condition=models.Q(actor_access_epoch__gte=0),
                name="s014_course_actor_epoch_nonnegative",
            ),
        ),
    ]