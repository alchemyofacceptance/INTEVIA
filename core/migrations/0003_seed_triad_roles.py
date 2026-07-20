from django.db import migrations

# ---------------------------------------------------------
# create_triad_roles
# ---------------------------------------------------------
# This function seeds the three foundational roles of the
# Human–AI Triad into the database:
#
#   - User : the human participant
#   - Gee  : the meaning/interpretation layer
#   - Coe  : the implementation/execution layer
#
# These roles form the ontological backbone of INTEVIA.
# They are inserted using get_or_create() to ensure the
# migration is idempotent (safe to run multiple times).
# ---------------------------------------------------------
def create_triad_roles(apps, schema_editor):
    Role = apps.get_model('core', 'Role')

    roles = [
        ("User", "Human participant in the Triad"),
        ("Gee", "Meaning layer of the Triad"),
        ("Coe", "Implementation layer of the Triad"),
    ]

    for name, description in roles:
        # Create the role if it does not already exist.
        Role.objects.get_or_create(
            name=name,
            defaults={"description": description}
        )


# ---------------------------------------------------------
# remove_triad_roles
# ---------------------------------------------------------
# Reverse operation for the migration.
# Removes the three Triad roles if the migration is rolled
# back. This ensures clean reversibility and maintains
# Django migration integrity.
# ---------------------------------------------------------
def remove_triad_roles(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Role.objects.filter(name__in=["User", "Gee", "Coe"]).delete()


# ---------------------------------------------------------
# Migration Class
# ---------------------------------------------------------
# This migration depends on the initial core schema and
# inserts the Triad roles into the Role table.
#
# RunPython allows us to define both forward and reverse
# operations, ensuring the ontology is reproducible and
# reversible — a key HAT governance principle.
# ---------------------------------------------------------
class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_role_alter_profile_display_name_profilerole'),
    ]

    operations = [
        migrations.RunPython(create_triad_roles, remove_triad_roles),
    ]
