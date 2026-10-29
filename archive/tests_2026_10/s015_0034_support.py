import re


_FUNCTIONS = (
    "s3fu_application_settings_basis_valid",
    "s3fu_guard_application_engagement_basis",
    "s3_decision_guard_a26",
    "s3_decision_guard",
    "s015_authoritydecision_guard",
    "s015_refuse_row_mutation",
)

_TRIGGERS = (
    "s3_05_engagement_settings_basis_update",
    "s3_12_engagement_settings_basis_insert",
    "s3_20_s3_decision_guard",
    "s015_authoritydecision_insert_guard",
    "s015_authoritydecision_immutable",
    "s015_authoritydecision_truncate_immutable",
)


def emit_guard_catalogue(connection, phase):
    with connection.cursor() as cursor:
        for name in _FUNCTIONS:
            cursor.execute(
                "SELECT pg_get_function_identity_arguments(p.oid), pg_get_functiondef(p.oid) "
                "FROM pg_catalog.pg_proc p "
                "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = %s "
                "ORDER BY pg_get_function_identity_arguments(p.oid)",
                [name],
            )
            rows = cursor.fetchall()
            if not rows:
                print(f"CATALOGUE:{phase}|FUNCTION|public.{name}|ABSENT", flush=True)
            for arguments, definition in rows:
                one_line = re.sub(r"\s+", " ", definition).strip()
                print(
                    f"CATALOGUE:{phase}|FUNCTION|public.{name}({arguments})|{one_line}",
                    flush=True,
                )
        for name in _TRIGGERS:
            cursor.execute(
                "SELECT c.relname, t.tgenabled, p.proname, pg_get_triggerdef(t.oid) "
                "FROM pg_catalog.pg_trigger t "
                "JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid "
                "WHERE n.nspname = 'public' AND t.tgname = %s AND NOT t.tgisinternal",
                [name],
            )
            rows = cursor.fetchall()
            if not rows:
                print(f"CATALOGUE:{phase}|TRIGGER|{name}|ABSENT", flush=True)
            for table, enabled, function, definition in rows:
                one_line = re.sub(r"\s+", " ", definition).strip()
                print(
                    f"CATALOGUE:{phase}|TRIGGER|public.{table}.{name}|enabled={enabled}|"
                    f"function=public.{function}|{one_line}",
                    flush=True,
                )
