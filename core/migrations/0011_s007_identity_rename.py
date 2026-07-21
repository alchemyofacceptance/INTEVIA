import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


PERMISSION_ACTIONS = ("add", "change", "delete", "view")


def reconcile_identity_content_type(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    old = ContentType.objects.filter(app_label="core", model="profile").first()
    current = ContentType.objects.filter(
        app_label="core", model="identity"
    ).first()
    if old is not None and current is not None and old.pk != current.pk:
        raise RuntimeError("both Profile and Identity content types exist")
    content_type = current or old
    if content_type is None:
        return
    if content_type.model != "identity":
        content_type.model = "identity"
        content_type.save(update_fields=("model",))

    for action in PERMISSION_ACTIONS:
        old_codename = f"{action}_profile"
        new_codename = f"{action}_identity"
        old_permission = Permission.objects.filter(
            content_type=content_type,
            codename=old_codename,
        ).first()
        new_permission = Permission.objects.filter(
            content_type=content_type,
            codename=new_codename,
        ).first()
        if (
            old_permission is not None
            and new_permission is not None
            and old_permission.pk != new_permission.pk
        ):
            raise RuntimeError(
                f"both {old_codename} and {new_codename} permissions exist"
            )
        permission = new_permission or old_permission
        if permission is not None:
            permission.codename = new_codename
            permission.name = f"Can {action} identity"
            permission.save(update_fields=("codename", "name"))


def restore_profile_content_type(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    old = ContentType.objects.filter(app_label="core", model="identity").first()
    current = ContentType.objects.filter(
        app_label="core", model="profile"
    ).first()
    if old is not None and current is not None and old.pk != current.pk:
        raise RuntimeError("both Identity and Profile content types exist")
    content_type = current or old
    if content_type is None:
        return
    if content_type.model != "profile":
        content_type.model = "profile"
        content_type.save(update_fields=("model",))

    for action in PERMISSION_ACTIONS:
        old_codename = f"{action}_identity"
        new_codename = f"{action}_profile"
        permission = Permission.objects.filter(
            content_type=content_type,
            codename=old_codename,
        ).first()
        if permission is not None:
            permission.codename = new_codename
            permission.name = f"Can {action} profile"
            permission.save(update_fields=("codename", "name"))


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0010_event_registration_foundation"),
    ]

    operations = [
        migrations.RenameModel(old_name="Profile", new_name="Identity"),
        migrations.RenameField(
            model_name="identity",
            old_name="user",
            new_name="credential",
        ),
        migrations.AlterField(
            model_name="identity",
            name="credential",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="intevia_identity",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterUniqueTogether(
            name="profilerole",
            unique_together=set(),
        ),
        migrations.RenameField(
            model_name="profilerole",
            old_name="profile",
            new_name="identity",
        ),
        migrations.AlterUniqueTogether(
            name="profilerole",
            unique_together={("identity", "role")},
        ),
        migrations.RunPython(
            reconcile_identity_content_type,
            restore_profile_content_type,
        ),
    ]