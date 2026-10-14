"""Use statement-start knowledge for platform rules in the entitlement limit.

Implements Human Governor ruling HG_RULING_O1_O3_AND_PKT_CLK_1_20261014,
O-3 option (b): a platform rule must be recorded no later than the start of
the statement evaluating the allowance. ``statement_timestamp()`` is constant
within a statement, so the function's ``STABLE`` declaration remains honest;
``clock_timestamp()`` could change during one statement.
"""

from importlib import import_module

from django.db import migrations


_SE_MIGRATION = import_module("core.migrations.0046_se_entitlement_foundation")
_PREVIOUS_ENTITLEMENT_LIMIT_BODY = dict(_SE_MIGRATION.SE_FUNCTION_BODIES)[
    "s015_entitlement_limit"
]
_OLD_RECORDED_AT_PREDICATE = "rule.recorded_at <= CURRENT_TIMESTAMP"
_NEW_RECORDED_AT_PREDICATE = (
    "rule.recorded_at <= statement_timestamp() "
    "/* HG_RULING_O1_O3_AND_PKT_CLK_1_20261014, O-3 option (b) */"
)

if _PREVIOUS_ENTITLEMENT_LIMIT_BODY.count(_OLD_RECORDED_AT_PREDICATE) != 1:
    raise RuntimeError(
        "0046 entitlement-limit recorded-time predicate no longer matches SEK1"
    )

_ENTITLEMENT_LIMIT_BODY = _PREVIOUS_ENTITLEMENT_LIMIT_BODY.replace(
    _OLD_RECORDED_AT_PREDICATE, _NEW_RECORDED_AT_PREDICATE, 1
)


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "CREATE OR REPLACE FUNCTION public.s015_entitlement_limit("
            "p_living_organism_uuid uuid, p_dimension text, p_at timestamptz) "
            "RETURNS TABLE(kind text, value bigint) "
            "LANGUAGE plpgsql STABLE STRICT AS $$"
            + _ENTITLEMENT_LIMIT_BODY
            + "$$"
        )


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "CREATE OR REPLACE FUNCTION public.s015_entitlement_limit("
            "p_living_organism_uuid uuid, p_dimension text, p_at timestamptz) "
            "RETURNS TABLE(kind text, value bigint) "
            "LANGUAGE plpgsql STABLE STRICT AS $$"
            + _PREVIOUS_ENTITLEMENT_LIMIT_BODY
            + "$$"
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0046_se_entitlement_foundation")]

    operations = [migrations.RunPython(forwards, backwards)]
