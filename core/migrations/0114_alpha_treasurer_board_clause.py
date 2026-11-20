"""Align the Alpha Treasurer observation with the Board appointment wording."""

from importlib import import_module

from django.db import migrations

from core.migrations._clock_conversion import swap

# Interim until the CARE units replace the Alpha-specific rule. The qualifier
# hashes the full parsed clause, including the space after the approval locator.
OLD_FINGERPRINT = "742ce2ddbd4762c42d5b3c973b9aba2a889139d9f29423b2b147443d73e58823"
BOARD_FINGERPRINT = "16a2e23de25cba2ef7528fdd5e3f611f28aef1f15652caa1b572186338f6dfc9"
SIGNATURE = "public.care_alpha_treasurer_qualify(uuid,uuid,bigint,bigint,timestamptz,boolean)"


def replace_fingerprint(schema_editor, *, forward):
    definition = import_module("core.migrations.0111_care_office_record").QUALIFIER
    _, original, _ = definition.split("$qualifier$")
    if original.count(OLD_FINGERPRINT) != 1:
        raise RuntimeError("Expected one approved clause fingerprint in migration 0111.")
    revised = original.replace(OLD_FINGERPRINT, BOARD_FINGERPRINT)
    with schema_editor.connection.cursor() as cursor:
        if forward:
            swap(cursor, SIGNATURE, original, revised)
        else:
            swap(cursor, SIGNATURE, revised, original)


def install(apps, schema_editor):
    replace_fingerprint(schema_editor, forward=True)


def uninstall(apps, schema_editor):
    replace_fingerprint(schema_editor, forward=False)


class Migration(migrations.Migration):
    atomic = True
    dependencies = (("core", "0113_care_system_account"),)
    operations = (migrations.RunPython(install, uninstall),)
