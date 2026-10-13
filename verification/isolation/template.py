"""Template identity primitives and the ownership route for reusable test databases."""
import ast
import hashlib
import importlib.util
import json
import os
import platform
import re
from pathlib import Path
import sys
import uuid


class TemplateIdentityUnavailable(RuntimeError):
    """An identity input could not be determined, so the run must build fresh."""


class TemplateNamespaceUnavailable(RuntimeError):
    """A reusable template name requires a recognized store namespace."""


class TemplateReceiptInvalid(RuntimeError):
    """A persisted template receipt cannot prove the template's full identity."""


class TemplateNameCollision(RuntimeError):
    """A bounded database name is occupied by a different or unreceipted template."""


class TemplateSealingRefused(RuntimeError):
    """The role could not set the required ALLOW_CONNECTIONS false seal."""


def _required_template_namespace():
    namespace = os.environ.get("VERIFICATION_TEMPLATE_NAMESPACE")
    if namespace not in {"me", "vr"}:
        raise TemplateNamespaceUnavailable(
            "VERIFICATION_TEMPLATE_NAMESPACE must be set to 'me' or 'vr'"
        )
    return namespace


def canonical_listing(lines):
    """Return the specified sorted UTF-8 listing and lowercase SHA-256 identity."""
    lines = list(lines)
    if not lines:
        raise TemplateIdentityUnavailable("identity listing is empty")

    keys = set()
    for line in lines:
        if not isinstance(line, str) or "\n" in line or "\r" in line:
            raise TemplateIdentityUnavailable("identity input is not a single text line")
        key, separator, _ = line.partition("=")
        if not separator or not key:
            raise TemplateIdentityUnavailable("identity input is not a key=value line")
        if key in keys:
            raise TemplateIdentityUnavailable("duplicate identity key: %s" % key)
        keys.add(key)

    listing = "".join(line + "\n" for line in sorted(lines))
    identity = hashlib.sha256(listing.encode("utf-8")).hexdigest()
    return listing, identity


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_module_spec(module_name):
    try:
        return importlib.util.find_spec(module_name)
    except (ImportError, ModuleNotFoundError, ValueError):
        return None


def _migration_imports(path, package_name):
    tree = ast.parse(Path(path).read_bytes(), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative = "." * node.level + (node.module or "")
                try:
                    base_name = importlib.util.resolve_name(relative, package_name)
                except ImportError as exc:
                    raise TemplateIdentityUnavailable("cannot resolve migration import in %s" % path) from exc
            else:
                base_name = node.module
            if not base_name:
                continue
            names.add(base_name)
            for alias in node.names:
                candidate = base_name + "." + alias.name
                if alias.name != "*" and _find_module_spec(candidate) is not None:
                    names.add(candidate)
    return names


def _module_file(module_name):
    spec = _find_module_spec(module_name)
    if spec is None or not spec.origin or spec.origin in ("built-in", "frozen"):
        raise TemplateIdentityUnavailable("module %s has no determinable module file" % module_name)
    path = Path(spec.origin)
    if not path.is_file():
        raise TemplateIdentityUnavailable("module %s file is unavailable" % module_name)
    return path


def python_runtime_line(implementation=None, version_info=None, version_text=None):
    implementation = implementation or platform.python_implementation()
    version_info = version_info or sys.version_info
    version_text = version_text or sys.version
    dotted_version = ".".join(str(part) for part in version_info)
    return "software:python=%s-%s-%s" % (implementation, dotted_version, version_text.split()[0])


def module_input_lines(module_names):
    lines = []
    has_builtin = False
    for module_name in sorted(set(module_names)):
        spec = _find_module_spec(module_name)
        if spec is None:
            raise TemplateIdentityUnavailable("module %s cannot be resolved" % module_name)
        if spec.origin in ("built-in", "frozen"):
            lines.append("import:%s=builtin" % module_name)
            has_builtin = True
        else:
            module_path = _module_file(module_name)
            lines.append("import:%s=%s" % (module_name, _sha256_file(module_path)))
    if has_builtin:
        lines.append(python_runtime_line())
    return lines


def schema_setting_lines(installed_apps, auth_user_model, default_auto_field, use_tz, time_zone, test_options):
    values = {
        "INSTALLED_APPS": list(installed_apps),
        "AUTH_USER_MODEL": auth_user_model,
        "DEFAULT_AUTO_FIELD": default_auto_field,
        "USE_TZ": use_tz,
        "TIME_ZONE": time_zone,
    }
    lines = []
    try:
        for name, value in values.items():
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
            lines.append("setting:%s=%s" % (name, encoded))
        for name in ("CHARSET", "COLLATION"):
            encoded = json.dumps(test_options.get(name), sort_keys=True, separators=(",", ":"))
            lines.append("setting:DATABASES.default.TEST.%s=%s" % (name, encoded))
    except (TypeError, ValueError) as exc:
        raise TemplateIdentityUnavailable("schema-affecting settings are not JSON serializable") from exc
    return lines


def source_input_lines(repo_root=None):
    """Collect raw-byte migration/import/instrument hashes and source-level identity inputs."""
    from django.apps import apps
    from django.conf import settings
    import django

    root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
    expected_head = os.environ.get("VERIFICATION_EXPECTED_CORE_HEAD")
    if not expected_head:
        raise TemplateIdentityUnavailable("VERIFICATION_EXPECTED_CORE_HEAD is unset")

    migration_paths = []
    for app in apps.get_app_configs():
        directory = Path(app.path) / "migrations"
        if directory.is_dir():
            migration_paths.extend((app, path) for path in directory.glob("*.py") if path.stem != "__init__")
    if not migration_paths:
        raise TemplateIdentityUnavailable("no installed-app migration files were found")

    lines = []
    imported_modules = set()
    for app, path in migration_paths:
        relative = path.relative_to(app.path).as_posix()
        lines.append("migration:%s/%s=%s" % (app.label, relative, _sha256_file(path)))
        try:
            app_module = importlib.util.resolve_name("." + app.name.rsplit(".", 1)[-1], app.name.rpartition(".")[0])
        except ImportError:
            app_module = app.name
        migration_module = app_module + ".migrations." + path.stem
        package_name = migration_module.rpartition(".")[0]
        imported_modules.update(_migration_imports(path, package_name))

    lines.extend(module_input_lines(imported_modules))

    lines.append("head=%s" % expected_head)
    for relative in ("verification/isolation/state.py", "verification/isolation/runner.py"):
        path = root / Path(relative)
        if not path.is_file():
            raise TemplateIdentityUnavailable("instrument file is unavailable: %s" % relative)
        lines.append("instrument:%s=%s" % (relative, _sha256_file(path)))

    lines.extend(schema_setting_lines(
        settings.INSTALLED_APPS,
        settings.AUTH_USER_MODEL,
        settings.DEFAULT_AUTO_FIELD,
        settings.USE_TZ,
        settings.TIME_ZONE,
        settings.DATABASES["default"].get("TEST", {}),
    ))

    lines.append("software:django=%s" % django.get_version())
    try:
        import psycopg

        driver_version = psycopg.__version__
    except ImportError:
        try:
            import psycopg2

            driver_version = psycopg2.__version__
        except ImportError as exc:
            raise TemplateIdentityUnavailable("PostgreSQL driver version is unavailable") from exc
    lines.append("software:psycopg=%s" % driver_version)
    return lines


def runtime_input_lines(connection=None):
    """Read server, extension, owner and locale facts from the active build database."""
    _required_template_namespace()
    if connection is None:
        from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT pg_get_userbyid(datdba), pg_encoding_to_char(encoding), datcollate, datctype,
                          current_setting('server_version_num')
                   FROM pg_database WHERE datname = current_database()"""
            )
            row = cursor.fetchone()
            if row is None:
                raise TemplateIdentityUnavailable("active database properties are unavailable")
            cursor.execute("SELECT extname, extversion FROM pg_extension ORDER BY extname")
            extensions = cursor.fetchall()
    except TemplateIdentityUnavailable:
        raise
    except Exception as exc:
        raise TemplateIdentityUnavailable("database identity inputs could not be queried") from exc

    owner, encoding, lc_collate, lc_ctype, server_version_num = row
    lines = [
        "software:postgresql_server=%s" % server_version_num,
        "db:owner=%s" % owner,
        "db:encoding=%s" % encoding,
        "db:lc_collate=%s" % lc_collate,
        "db:lc_ctype=%s" % lc_ctype,
    ]
    lines.extend("software:extension:%s=%s" % (name, version) for name, version in extensions)
    return lines


def collect_template_identity(repo_root=None, connection=None):
    lines = source_input_lines(repo_root)
    lines.extend(runtime_input_lines(connection))
    return complete_identity(lines)


def complete_identity(lines):
    """Validate all commissioned input classes before emitting a listing and identity."""
    lines = list(lines)
    keys = {line.partition("=")[0] for line in lines if isinstance(line, str) and "=" in line}
    required = {
        "head",
        "instrument:verification/isolation/state.py",
        "instrument:verification/isolation/runner.py",
        "setting:INSTALLED_APPS",
        "setting:AUTH_USER_MODEL",
        "setting:DEFAULT_AUTO_FIELD",
        "setting:USE_TZ",
        "setting:TIME_ZONE",
        "setting:DATABASES.default.TEST.CHARSET",
        "setting:DATABASES.default.TEST.COLLATION",
        "software:django",
        "software:psycopg",
        "software:postgresql_server",
        "db:owner",
        "db:encoding",
        "db:lc_collate",
        "db:lc_ctype",
    }
    required_prefixes = ("migration:", "import:")
    missing = sorted(required - keys)
    if missing or any(not any(key.startswith(prefix) for key in keys) for prefix in required_prefixes):
        raise TemplateIdentityUnavailable("identity inputs are incomplete: %s" % missing)
    if any(line.startswith("import:") and line.endswith("=builtin") for line in lines) and "software:python" not in keys:
        raise TemplateIdentityUnavailable("built-in import requires software:python identity")
    return canonical_listing(lines)


def template_database_name(identity):
    namespace = _required_template_namespace()
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise TemplateIdentityUnavailable("template identity must be 64 lowercase hexadecimal characters")
    return "test_intevia_tpl_" + namespace + "_" + identity[:43]


def validate_template_receipt(receipt, expected_identity=None):
    listing = receipt.get("listing")
    identity = receipt.get("identity")
    if not isinstance(listing, str) or not isinstance(identity, str):
        raise TemplateReceiptInvalid("template receipt lacks the full listing or identity")
    try:
        canonical, computed_identity = canonical_listing(listing.splitlines())
    except TemplateIdentityUnavailable as exc:
        raise TemplateReceiptInvalid("template receipt listing is not canonical") from exc
    if canonical != listing or computed_identity != identity:
        raise TemplateReceiptInvalid("template receipt listing does not match its identity")
    if receipt.get("name") != template_database_name(identity):
        raise TemplateReceiptInvalid("template receipt name does not match the bounded identity name")
    if expected_identity is not None and identity != expected_identity:
        raise TemplateReceiptInvalid("template name prefix is associated with a different full identity")
    if not isinstance(receipt.get("oid"), int) or receipt["oid"] <= 0:
        raise TemplateReceiptInvalid("template receipt has no valid database OID")
    return True


def template_receipt_path(store, identity):
    return Path(store) / (template_database_name(identity) + ".json")


def read_template_receipt(path, expected_identity=None):
    try:
        with open(path, encoding="utf-8") as receipt_file:
            receipt = json.load(receipt_file)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TemplateReceiptInvalid("template receipt cannot be read") from exc
    validate_template_receipt(receipt, expected_identity)
    return receipt


def _database_record(connection, name):
    with connection.creation._nodb_cursor() as cursor:
        cursor.execute(
            """SELECT oid, pg_get_userbyid(datdba), pg_encoding_to_char(encoding), datcollate,
                      datctype, datallowconn, datistemplate
               FROM pg_database WHERE datname = %s""",
            [name],
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return {
        "oid": int(row[0]),
        "owner": row[1],
        "encoding": row[2],
        "lc_collate": row[3],
        "lc_ctype": row[4],
        "allow_connections": bool(row[5]),
        "is_template": bool(row[6]),
    }


def verify_registered_template(connection, receipt, expected_identity, expected_listing):
    validate_template_receipt(receipt, expected_identity)
    if receipt["listing"] != expected_listing:
        raise TemplateReceiptInvalid("template receipt listing differs from current identity inputs")
    database = _database_record(connection, receipt["name"])
    if database is None or database["oid"] != receipt["oid"]:
        raise TemplateReceiptInvalid("template database is absent or its OID differs from the receipt")
    if database["allow_connections"]:
        raise TemplateReceiptInvalid("template database is not sealed against connections")
    if receipt.get("is_template") and not database["is_template"]:
        raise TemplateReceiptInvalid("template IS_TEMPLATE state differs from its receipt")
    expected_properties = {
        "owner": _listing_value(expected_listing, "db:owner"),
        "encoding": _listing_value(expected_listing, "db:encoding"),
        "lc_collate": _listing_value(expected_listing, "db:lc_collate"),
        "lc_ctype": _listing_value(expected_listing, "db:lc_ctype"),
    }
    if any(database[key] != expected_properties[key] for key in expected_properties):
        raise TemplateReceiptInvalid("template database properties differ from its identity listing")
    return database


def _listing_value(listing, key):
    prefix = key + "="
    values = [line[len(prefix):] for line in listing.splitlines() if line.startswith(prefix)]
    if len(values) != 1:
        raise TemplateReceiptInvalid("identity listing must contain exactly one %s line" % key)
    return values[0]


def _write_template_receipt(path, receipt):
    validate_template_receipt(receipt, receipt["identity"])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as receipt_file:
        json.dump(receipt, receipt_file, ensure_ascii=True, separators=(",", ":"))
        receipt_file.write("\n")
        receipt_file.flush()
        os.fsync(receipt_file.fileno())


def _archive_absent_template_receipt(connection, path, receipt, identity):
    validate_template_receipt(receipt, identity)
    persisted = read_template_receipt(path, identity)
    if persisted != receipt:
        raise TemplateReceiptInvalid("template receipt changed before stale-receipt archival")
    if _database_record(connection, receipt["name"]) is not None:
        raise TemplateReceiptInvalid("refusing to archive a receipt while its database name is occupied")
    attempt_receipt_path = os.environ.get("VERIFICATION_DB_RECEIPT")
    nonce = os.environ.get("VERIFICATION_RUN_NONCE")
    if not attempt_receipt_path or not nonce:
        raise TemplateReceiptInvalid("attempt receipt path and nonce are required to archive a stale template")
    archive_path = Path(attempt_receipt_path).parent / (
        "orphan-template-receipt-%s-%s.json" % (receipt["name"], nonce)
    )
    if archive_path.exists():
        raise TemplateReceiptInvalid("orphan template receipt archive path already exists")
    Path(path).rename(archive_path)
    _attempt_receipt("template_receipt_archived", name=receipt["name"], oid=receipt["oid"],
                     identity=identity, receipt=str(path), archive=str(archive_path),
                     database_confirmed_absent=True)
    return archive_path


def _attempt_receipt(event, **fields):
    from verification.ownership import write_receipt

    write_receipt(event, **fields)


def _create_database(connection, name, template_name=None):
    record = _database_record(connection, name)
    if record is not None:
        raise TemplateNameCollision("database name %s already exists" % name)
    quote = connection.ops.quote_name
    _attempt_receipt("template_create_attempt", name=name, template=template_name)
    statement = "CREATE DATABASE %s%s" % (quote(name), connection.creation.sql_table_creation_suffix())
    if template_name:
        statement += " TEMPLATE %s" % quote(template_name)
    with connection.creation._nodb_cursor() as cursor:
        cursor.execute(statement)
    record = _database_record(connection, name)
    if record is None:
        raise RuntimeError("database %s was not visible after creation" % name)
    _attempt_receipt("template_database_created", name=name, oid=record["oid"], template=template_name)
    return record


def _drop_database_by_oid(connection, name, oid, clear_is_template=False):
    record = _database_record(connection, name)
    if record is None:
        return True
    if record["oid"] != oid:
        raise TemplateReceiptInvalid("refusing to drop %s: current OID differs from its receipt" % name)
    quote = connection.ops.quote_name
    with connection.creation._nodb_cursor() as cursor:
        if clear_is_template and record["is_template"]:
            cursor.execute("ALTER DATABASE %s WITH IS_TEMPLATE false" % quote(name))
        cursor.execute("DROP DATABASE %s" % quote(name))
    return _database_record(connection, name) is None


def _migrate_build_database(connection, alias, name):
    from django.conf import settings
    from django.core.management import call_command

    old_connection_name = connection.settings_dict["NAME"]
    old_settings_name = settings.DATABASES[alias]["NAME"]
    connection.close()
    connection.settings_dict["NAME"] = name
    settings.DATABASES[alias]["NAME"] = name
    try:
        call_command("migrate", verbosity=0, interactive=False, database=alias, run_syncdb=True)
        call_command("createcachetable", verbosity=0, database=alias)
        from intevia.test_postgresql_backend.operations import S015_TRUNCATE_GUARDIANS
        from verification.isolation import state

        reference = state.capture()
        violations = state.check_requirements(reference, S015_TRUNCATE_GUARDIANS)
        if violations:
            raise TemplateIdentityUnavailable("fresh template build did not meet CP-0: %s" % violations[0])
        return reference, state.digest(reference), runtime_input_lines(connection)
    finally:
        connection.close()
        connection.settings_dict["NAME"] = old_connection_name
        settings.DATABASES[alias]["NAME"] = old_settings_name


def _connection_error_details(exc, parameters):
    current = exc
    sqlstate = None
    while current is not None and sqlstate is None:
        diagnostic = getattr(current, "diag", None)
        sqlstate = (getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
                    or getattr(diagnostic, "sqlstate", None))
        current = current.__cause__ or current.__context__
    message = str(exc)
    password = parameters.get("password")
    if password:
        message = message.replace(password, "[redacted]")
    return sqlstate, message


def _refuse_template_connections(connection, name, template_oid):
    control_name = "test_intevia_ctl_" + uuid.uuid4().hex[:20]
    control_database = _create_database(connection, control_name)
    control_connection = None
    catalog_connection = None
    direct_connection = None
    parameters = connection.get_connection_params()
    try:
        if control_database["is_template"] or not control_database["allow_connections"]:
            raise TemplateSealingRefused("owned control database is not connectable and non-template")

        catalog_parameters = dict(parameters)
        catalog_parameters["dbname"] = control_name
        catalog_connection = connection.Database.connect(**catalog_parameters)
        with catalog_connection.cursor() as cursor:
            cursor.execute("SELECT datallowconn FROM pg_database WHERE oid = %s", [template_oid])
            catalog_row = cursor.fetchone()
        catalog_datallowconn = None if catalog_row is None else catalog_row[0]
        if catalog_datallowconn is not False:
            _attempt_receipt(
                "template_connection_refusal_result", name=name, oid=template_oid,
                control_name=control_name, control_oid=control_database["oid"],
                catalog_connection_connected=True, catalog_datallowconn=catalog_datallowconn,
                control_connected=False, refusal=False,
            )
            raise TemplateSealingRefused("catalog witness did not confirm datallowconn false")
        catalog_connection.close()
        catalog_connection = None

        control_parameters = dict(parameters)
        control_parameters["dbname"] = control_name
        control_connection = connection.Database.connect(**control_parameters)
        template_parameters = dict(parameters)
        template_parameters["dbname"] = name
        try:
            direct_connection = connection.Database.connect(**template_parameters)
        except connection.Database.OperationalError as exc:
            sqlstate, message = _connection_error_details(exc, template_parameters)
            _attempt_receipt(
                "template_connection_refusal_result", name=name, oid=template_oid,
                control_name=control_name, control_oid=control_database["oid"],
                catalog_connection_connected=True, catalog_datallowconn=False,
                control_connected=True, refusal=True, error_type=type(exc).__name__,
                sqlstate=sqlstate, message=message,
            )
            return {
                "connection_refused": True,
                "connection_refusal_sqlstate": sqlstate,
                "connection_refusal_message": message,
                "connection_refusal_control_database": control_name,
                "connection_refusal_control_oid": control_database["oid"],
                "connection_refusal_catalog_datallowconn": False,
                "connection_refusal_catalog_connected": True,
                "connection_refusal_control_connected": True,
            }
        except Exception as exc:
            sqlstate, message = _connection_error_details(exc, template_parameters)
            _attempt_receipt(
                "template_connection_refusal_result", name=name, oid=template_oid,
                control_name=control_name, control_oid=control_database["oid"],
                catalog_connection_connected=True, catalog_datallowconn=False,
                control_connected=True, refusal=False, error_type=type(exc).__name__,
                sqlstate=sqlstate, message=message,
            )
            raise TemplateSealingRefused("template probe did not raise OperationalError") from None
        else:
            direct_connection.close()
            direct_connection = None
            _attempt_receipt(
                "template_connection_refusal_result", name=name, oid=template_oid,
                control_name=control_name, control_oid=control_database["oid"],
                catalog_connection_connected=True, catalog_datallowconn=False,
                control_connected=True, refusal=False,
            )
            raise TemplateSealingRefused("sealed template accepted a direct connection")
    finally:
        for owned_connection in (direct_connection, control_connection, catalog_connection):
            if owned_connection is not None:
                owned_connection.close()
        absent = _drop_database_by_oid(connection, control_name, control_database["oid"])
        _attempt_receipt("template_control_database_destroyed", name=control_name,
                         oid=control_database["oid"], confirmed_absent=absent)
        if not absent:
            raise TemplateReceiptInvalid("owned control database cleanup was not confirmed")


def _build_template(connection, alias, store, source_lines):
    from django.conf import settings

    nonce = os.environ["VERIFICATION_RUN_NONCE"]
    original_connection_name = connection.settings_dict["NAME"]
    original_settings_name = settings.DATABASES[alias]["NAME"]
    bootstrap_name = "test_intevia_boot_" + uuid.uuid4().hex[:20]
    bootstrap = _create_database(connection, bootstrap_name)
    working_name = bootstrap_name
    retained = False
    target_path = None
    target_identity = None
    template_receipt_written = False
    try:
        _, cp0_digest, runtime_lines = _migrate_build_database(connection, alias, bootstrap_name)
        listing, identity = complete_identity(source_lines + runtime_lines)
        target_identity = identity
        target_name = template_database_name(identity)
        target_path = template_receipt_path(store, identity)
        existing_database = _database_record(connection, target_name)
        existing_receipt = read_template_receipt(target_path)
        if existing_database is not None:
            if existing_receipt is None:
                raise TemplateNameCollision("bounded template name exists without an identity receipt")
            validate_template_receipt(existing_receipt, identity)
            verify_registered_template(connection, existing_receipt, identity, listing)
            if not _drop_database_by_oid(connection, bootstrap_name, bootstrap["oid"]):
                raise TemplateReceiptInvalid("temporary build database cleanup was not confirmed")
            _attempt_receipt("template_bootstrap_destroyed", name=bootstrap_name, oid=bootstrap["oid"],
                             confirmed_absent=True)
            retained = True
            return existing_receipt, False
        if existing_receipt is not None:
            raise TemplateNameCollision("template receipt exists but its database is absent")

        quote = connection.ops.quote_name
        with connection.creation._nodb_cursor() as cursor:
            cursor.execute("ALTER DATABASE %s RENAME TO %s" % (quote(bootstrap_name), quote(target_name)))
        working_name = target_name
        try:
            with connection.creation._nodb_cursor() as cursor:
                cursor.execute("ALTER DATABASE %s WITH ALLOW_CONNECTIONS false" % quote(target_name))
        except Exception:
            raise TemplateSealingRefused("role could not set required ALLOW_CONNECTIONS false seal") from None

        is_template = False
        try:
            with connection.creation._nodb_cursor() as cursor:
                cursor.execute("ALTER DATABASE %s WITH IS_TEMPLATE true" % quote(target_name))
            is_template = True
        except Exception as exc:
            _attempt_receipt("template_is_template_unavailable", name=target_name,
                             error_type=type(exc).__name__)

        database = _database_record(connection, target_name)
        if database is None or database["oid"] != bootstrap["oid"] or database["allow_connections"]:
            raise TemplateSealingRefused("template ALLOW_CONNECTIONS seal was not established")
        refusal_witness = _refuse_template_connections(connection, target_name, database["oid"])
        receipt = {
            "identity": identity,
            "listing": listing,
            "name": target_name,
            "oid": database["oid"],
            "run_nonce": nonce,
            "cp0_digest": cp0_digest,
            "fresh_build_cp0_digest": cp0_digest,
            "allow_connections": False,
            "is_template": is_template,
        }
        receipt.update(refusal_witness)
        _write_template_receipt(target_path, receipt)
        template_receipt_written = True
        _attempt_receipt("template_created", name=target_name, oid=database["oid"], identity=identity,
                         receipt=str(target_path), cp0_digest=cp0_digest,
                         allow_connections=False, is_template=is_template,
                 connection_refused=refusal_witness["connection_refused"],
                 catalog_datallowconn=refusal_witness["connection_refusal_catalog_datallowconn"],
                 control_connected=refusal_witness["connection_refusal_control_connected"])
        retained = True
        return receipt, True
    finally:
        if not retained:
            connection.close()
            settings.DATABASES[alias]["NAME"] = original_settings_name
            connection.settings_dict["NAME"] = original_connection_name
            absent = _drop_database_by_oid(connection, working_name, bootstrap["oid"], clear_is_template=True)
            _attempt_receipt("template_build_database_destroyed", name=working_name, oid=bootstrap["oid"],
                             confirmed_absent=absent)
            if not absent:
                raise TemplateReceiptInvalid("template build database cleanup was not confirmed")
            if template_receipt_written:
                written_receipt = read_template_receipt(target_path, target_identity)
                if written_receipt is None or written_receipt["oid"] != bootstrap["oid"]:
                    raise TemplateReceiptInvalid("new template receipt does not match the destroyed build OID")
                _archive_absent_template_receipt(connection, target_path, written_receipt, target_identity)


def prepare_template(connection, alias, store):
    _required_template_namespace()
    source_lines = source_input_lines()
    expected_listing, expected_identity = complete_identity(source_lines + runtime_input_lines(connection))
    path = template_receipt_path(store, expected_identity)
    Path(store).mkdir(parents=True, exist_ok=True)
    receipt = read_template_receipt(path, expected_identity)
    if receipt is not None and _database_record(connection, receipt["name"]) is None:
        _archive_absent_template_receipt(connection, path, receipt, expected_identity)
        receipt = None
    if receipt is not None:
        verify_registered_template(connection, receipt, expected_identity, expected_listing)
        return receipt, False
    if _database_record(connection, template_database_name(expected_identity)) is not None:
        raise TemplateNameCollision("bounded template name exists without a matching full-identity receipt")
    return _build_template(connection, alias, store, source_lines)


def create_clone_database(connection, template_receipt, test_database_name):
    from verification.ownership import CollisionRefused

    listing, identity = collect_template_identity(connection=connection)
    if identity != template_receipt["identity"] or listing != template_receipt["listing"]:
        raise TemplateIdentityUnavailable("current inputs differ from the recorded template identity")
    verify_registered_template(connection, template_receipt, identity, listing)
    protected = {"intevia", "intevia_dev", os.environ.get("INTEVIA_POSTGRES_DB")}
    if test_database_name in protected:
        raise CollisionRefused("protected database name refused for clone")
    if _database_record(connection, test_database_name) is not None:
        raise CollisionRefused("clone database %s already exists; refusing reuse" % test_database_name)

    _attempt_receipt("create_attempt", name=test_database_name, template=template_receipt["name"],
                     template_oid=template_receipt["oid"], identity=identity)
    quote = connection.ops.quote_name
    with connection.creation._nodb_cursor() as cursor:
        cursor.execute("CREATE DATABASE %s TEMPLATE %s" %
                       (quote(test_database_name), quote(template_receipt["name"])))
    clone = _database_record(connection, test_database_name)
    if clone is None:
        raise RuntimeError("clone database was not visible after creation")
    _attempt_receipt("created", name=test_database_name, oid=clone["oid"])
    _attempt_receipt("clone_created", name=test_database_name, clone_oid=clone["oid"],
                     template_name=template_receipt["name"], template_oid=template_receipt["oid"],
                     identity=identity, template_cp0_digest=template_receipt["cp0_digest"],
                     template_receipt=str(template_receipt_path(
                         os.environ["VERIFICATION_TEMPLATE_STORE"], identity)))
    return test_database_name


def install_clone_creation(creation, template_receipt):
    from verification.ownership import CollisionRefused

    def _create_test_db(verbosity, autoclobber, keepdb=False):
        if keepdb:
            raise CollisionRefused("keepdb is not permitted on the template clone route")
        name = creation._get_test_db_name()
        return create_clone_database(creation.connection, template_receipt, name)

    creation._create_test_db = _create_test_db


def record_final_clone_cp0(receipt_path, identity, clone_digest):
    receipt = read_template_receipt(receipt_path, identity)
    if receipt is None:
        raise TemplateReceiptInvalid("template receipt is absent while recording final clone CP-0")
    if clone_digest != receipt.get("cp0_digest"):
        raise TemplateReceiptInvalid("final clone CP-0 differs from the template digest")
    receipt["final_clone_cp0_digest"] = clone_digest
    temporary = str(receipt_path) + ".tmp." + os.environ["VERIFICATION_RUN_NONCE"]
    with open(temporary, "x", encoding="utf-8", newline="\n") as receipt_file:
        json.dump(receipt, receipt_file, ensure_ascii=True, separators=(",", ":"))
        receipt_file.write("\n")
        receipt_file.flush()
        os.fsync(receipt_file.fileno())
    os.replace(temporary, receipt_path)


def cleanup_template(connection, receipt_path, identity, allow_incomplete_parity=False):
    receipt = read_template_receipt(receipt_path, identity)
    if receipt is None:
        raise TemplateReceiptInvalid("template receipt is absent during cleanup")
    validate_template_receipt(receipt, identity)
    if not receipt.get("connection_refused") or not receipt.get("allow_connections") is False:
        raise TemplateReceiptInvalid("template seal and direct-connection witness are incomplete")
    final_digest = receipt.get("final_clone_cp0_digest")
    if final_digest is not None and final_digest != receipt.get("cp0_digest"):
        raise TemplateReceiptInvalid("final clone CP-0 does not match the template's recorded digest")
    if final_digest is None and not allow_incomplete_parity:
        raise TemplateReceiptInvalid("final clone CP-0 has not been recorded")

    database = _database_record(connection, receipt["name"])
    if database is None or database["oid"] != receipt["oid"] or database["allow_connections"]:
        raise TemplateReceiptInvalid("template OID or sealed state differs from its ownership receipt")
    _attempt_receipt("template_destroy_attempt", name=receipt["name"], oid=receipt["oid"],
                     created_nonce=receipt["run_nonce"], identity=identity)
    try:
        absent = _drop_database_by_oid(connection, receipt["name"], receipt["oid"], clear_is_template=True)
    except Exception:
        raise TemplateReceiptInvalid("template cleanup through its ownership receipt failed") from None
    if not absent:
        raise TemplateReceiptInvalid("template absence could not be confirmed")

    receipt["destroyed"] = {
        "oid": receipt["oid"],
        "run_nonce": receipt["run_nonce"],
        "cleanup_run_nonce": os.environ["VERIFICATION_RUN_NONCE"],
        "confirmed_absent": True,
    }
    temporary = str(receipt_path) + ".tmp." + os.environ["VERIFICATION_RUN_NONCE"]
    with open(temporary, "x", encoding="utf-8", newline="\n") as receipt_file:
        json.dump(receipt, receipt_file, ensure_ascii=True, separators=(",", ":"))
        receipt_file.write("\n")
        receipt_file.flush()
        os.fsync(receipt_file.fileno())
    os.replace(temporary, receipt_path)
    _attempt_receipt("template_destroyed", name=receipt["name"], oid=receipt["oid"],
                     created_nonce=receipt["run_nonce"], confirmed_absent=True)
    return True