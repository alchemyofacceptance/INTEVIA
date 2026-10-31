"""Template identity and recovery for TU-1b under TU1B_BUILD_COMMISSION_v1_2."""
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


def _required_database_tag():
    tag = os.environ.get("INTEVIA_DATABASE_TAG")
    if not isinstance(tag, str) or re.fullmatch(r"[a-z0-9]{1,8}", tag) is None:
        raise TemplateIdentityUnavailable("INTEVIA_DATABASE_TAG must be a lowercase tag of at most 8 characters")
    return tag


def _required_evidence_root():
    evidence_root = os.environ.get("INTEVIA_EVIDENCE_ROOT")
    if not isinstance(evidence_root, str) or re.fullmatch(r"PKTB_[A-Z0-9_]{1,24}", evidence_root) is None:
        raise TemplateIdentityUnavailable("INTEVIA_EVIDENCE_ROOT must name the authorized evidence root")
    return evidence_root


def _required_candidate_binding():
    candidate_binding = os.environ.get("INTEVIA_CANDIDATE_BINDING_SHA256")
    if not isinstance(candidate_binding, str) or re.fullmatch(r"[0-9a-f]{64}", candidate_binding) is None:
        raise TemplateIdentityUnavailable("candidate binding is required before template use")
    return candidate_binding


def _tag_register_digest(record):
    payload = {
        "version": record["version"],
        "sequence": record["sequence"],
        "database_tag": record["database_tag"],
        "evidence_root": record["evidence_root"],
        "previous_sha256": record["previous_sha256"],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def register_database_tag(path, database_tag, evidence_root):
    """Apply TU1-T1: bind one launch tag to one evidence root in the staging register."""
    if not isinstance(database_tag, str) or re.fullmatch(r"[a-z0-9]{1,8}", database_tag) is None:
        raise TemplateIdentityUnavailable("database tag is invalid")
    if not isinstance(evidence_root, str) or re.fullmatch(r"PKTB_[A-Z0-9_]{1,24}", evidence_root) is None:
        raise TemplateIdentityUnavailable("evidence root is invalid")
    destination = Path(path)
    if not destination.parent.is_dir() or destination.is_symlink():
        raise TemplateReceiptInvalid("database tag register path is absent or symbolic")
    with open(destination, "a+", encoding="utf-8", newline="\n") as register:
        if os.name == "nt":
            import msvcrt

            register.seek(0)
            try:
                msvcrt.locking(register.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("database tag register is locked; launch refused") from exc
        else:
            import fcntl

            try:
                fcntl.flock(register.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("database tag register is locked; launch refused") from exc
        try:
            register.seek(0)
            raw = register.read()
            if raw and not raw.endswith("\n"):
                raise TemplateReceiptInvalid("database tag register ends with a partial record")
            entries = []
            previous = "0" * 64
            for number, line in enumerate(raw.splitlines(), 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise TemplateReceiptInvalid("database tag register line %d is invalid JSON" % number) from exc
                if (
                    not isinstance(record, dict)
                    or set(record) != {
                        "version", "sequence", "database_tag", "evidence_root",
                        "previous_sha256", "record_sha256",
                    }
                    or type(record.get("version")) is not int
                    or record.get("version") != 1
                    or type(record.get("sequence")) is not int
                    or record.get("sequence") != number
                    or not isinstance(record.get("database_tag"), str)
                    or not isinstance(record.get("evidence_root"), str)
                    or not isinstance(record.get("previous_sha256"), str)
                    or not isinstance(record.get("record_sha256"), str)
                    or record.get("previous_sha256") != previous
                    or record.get("record_sha256") != _tag_register_digest(record)
                    or re.fullmatch(r"[a-z0-9]{1,8}", str(record.get("database_tag", ""))) is None
                    or re.fullmatch(r"PKTB_[A-Z0-9_]{1,24}", str(record.get("evidence_root", ""))) is None
                ):
                    raise TemplateReceiptInvalid("database tag register hash chain is invalid at line %d" % number)
                entries.append(record)
                previous = record["record_sha256"]

            tag_roots = {entry["database_tag"]: entry["evidence_root"] for entry in entries}
            root_tags = {entry["evidence_root"]: entry["database_tag"] for entry in entries}
            if len(tag_roots) != len(entries) or len(root_tags) != len(entries):
                raise TemplateReceiptInvalid("database tag register contains duplicate bindings")
            prior_root = tag_roots.get(database_tag)
            prior_tag = root_tags.get(evidence_root)
            if (prior_root is not None and prior_root != evidence_root) or (
                prior_tag is not None and prior_tag != database_tag
            ):
                raise RuntimeError("database tag or evidence root is already bound differently; launch refused")
            if prior_root is None and prior_tag is None:
                record = {
                    "version": 1,
                    "sequence": len(entries) + 1,
                    "database_tag": database_tag,
                    "evidence_root": evidence_root,
                    "previous_sha256": previous,
                }
                record["record_sha256"] = _tag_register_digest(record)
                register.seek(0, os.SEEK_END)
                register.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                register.flush()
                os.fsync(register.fileno())
                status = "BOUND"
            else:
                status = "EXISTING"
            register.seek(0)
            digest = hashlib.sha256(register.read().encode("utf-8")).hexdigest()
            return {"status": status, "sha256": digest}
        finally:
            if os.name == "nt":
                register.seek(0)
                msvcrt.locking(register.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(register.fileno(), fcntl.LOCK_UN)


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


def _template_database_name(identity, namespace, tag):
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise TemplateIdentityUnavailable("template identity must be 64 lowercase hexadecimal characters")
    if namespace not in {"me", "vr"}:
        raise TemplateNamespaceUnavailable("template namespace must be 'me' or 'vr'")
    if re.fullmatch(r"[a-z0-9]{1,8}", tag) is None:
        raise TemplateIdentityUnavailable("database tag is invalid")
    # TU1-T1: bind the bounded name to its registered unit tag while retaining at least 32 identity characters.
    name = "test_intevia_tpl_" + namespace + "_" + tag + "_" + identity[:32]
    if len(name.encode("ascii")) > 63:
        raise TemplateIdentityUnavailable("unit-bound template name exceeds PostgreSQL's 63-byte limit")
    return name


def template_database_name(identity):
    return _template_database_name(
        identity, _required_template_namespace(), _required_database_tag()
    )


def validate_template_receipt(receipt, expected_identity=None, namespace=None):
    if not isinstance(receipt, dict):
        raise TemplateReceiptInvalid("template receipt is not a JSON object")
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
    receipt_namespace = _required_template_namespace() if namespace is None else namespace
    if receipt.get("name") != _template_database_name(
        identity, receipt_namespace, _required_database_tag()
    ):
        raise TemplateReceiptInvalid("template receipt name does not match the bounded identity name")
    if receipt.get("database_tag") != _required_database_tag():
        raise TemplateReceiptInvalid("template receipt belongs to a different database tag")
    if receipt.get("evidence_root") != _required_evidence_root():
        raise TemplateReceiptInvalid("template receipt belongs to a different evidence root")
    if not isinstance(receipt.get("run_nonce"), str) or re.fullmatch(
        r"[0-9a-f]{32}", receipt["run_nonce"]
    ) is None:
        raise TemplateReceiptInvalid("template receipt has no valid owning run nonce")
    if not isinstance(receipt.get("candidate_binding"), str) or re.fullmatch(
        r"[0-9a-f]{64}", receipt["candidate_binding"]
    ) is None:
        raise TemplateReceiptInvalid("template receipt has no valid candidate binding")
    build_intent = receipt.get("build_intent")
    if (
        not isinstance(build_intent, dict)
        or set(build_intent) != {"path", "identity", "run_nonce", "database_oid"}
        or not isinstance(build_intent.get("path"), str)
        or not build_intent["path"]
        or build_intent.get("identity") != identity
        or build_intent.get("run_nonce") != receipt["run_nonce"]
        or type(build_intent.get("database_oid")) is not int
        or build_intent.get("database_oid") != receipt["oid"]
    ):
        raise TemplateReceiptInvalid("template receipt does not identify its completed build intent")
    if expected_identity is not None and identity != expected_identity:
        raise TemplateReceiptInvalid("template name prefix is associated with a different full identity")
    if type(receipt.get("oid")) is not int or receipt["oid"] <= 0:
        raise TemplateReceiptInvalid("template receipt has no valid database OID")
    return True


def template_receipt_path(store, identity):
    return Path(store) / (template_database_name(identity) + ".json")


def read_template_receipt(path, expected_identity=None, namespace=None):
    try:
        with open(path, encoding="utf-8") as receipt_file:
            receipt = json.load(receipt_file)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TemplateReceiptInvalid("template receipt cannot be read") from exc
    validate_template_receipt(receipt, expected_identity, namespace)
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
    with connection.creation._nodb_cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM pg_stat_activity WHERE datid = %s AND pid <> pg_backend_pid()",
            [int(row[0])],
        )
        active_connections = int(cursor.fetchone()[0])
    return {
        "oid": int(row[0]),
        "owner": row[1],
        "encoding": row[2],
        "lc_collate": row[3],
        "lc_ctype": row[4],
        "allow_connections": bool(row[5]),
        "is_template": bool(row[6]),
        "active_connections": active_connections,
    }


def verify_registered_template(connection, receipt, expected_identity, expected_listing, namespace=None):
    validate_template_receipt(receipt, expected_identity, namespace)
    if receipt.get("destroyed") is not None:
        raise TemplateReceiptInvalid("template receipt was already consumed by cleanup")
    if receipt["listing"] != expected_listing:
        raise TemplateReceiptInvalid("template receipt listing differs from current identity inputs")
    _verify_template_build_intent(receipt)
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


def _verify_template_build_intent(receipt):
    reference = receipt["build_intent"]
    evidence_root_path = os.environ.get("INTEVIA_EVIDENCE_ROOT_PATH")
    if not evidence_root_path or not _path_is_within(reference["path"], evidence_root_path):
        raise TemplateReceiptInvalid("template build intent is outside the authorized evidence root")
    try:
        with open(reference["path"], encoding="utf-8") as intent_file:
            intent = json.load(intent_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TemplateReceiptInvalid("template receipt's build intent cannot be read") from exc
    if (
        not isinstance(intent, dict)
        or type(intent.get("version")) is not int
        or intent.get("version") != 1
        or intent.get("database_tag") != receipt["database_tag"]
        or intent.get("evidence_root") != receipt["evidence_root"]
        or intent.get("namespace") != _template_namespace_from_name(receipt["name"])
        or intent.get("run_nonce") != reference["run_nonce"]
        or intent.get("identity") != reference["identity"]
        or intent.get("database_oid") != reference["database_oid"]
        or intent.get("target_name") != receipt["name"]
        or intent.get("candidate_binding") != receipt["candidate_binding"]
        or intent.get("stage") not in {
            "TEMPLATE_FLAGGED", "TEMPLATE_FLAG_UNAVAILABLE", "FINAL_RECEIPT",
            "ATTEMPT_RECEIPT", "COMPLETE",
        }
        or not isinstance(intent.get("attempt_receipt_path"), str)
        or not _path_is_within(intent.get("attempt_receipt_path"), evidence_root_path)
    ):
        raise TemplateReceiptInvalid("template receipt does not match its completed build intent")
    records = _read_attempt_records(intent.get("attempt_receipt_path"), reference["run_nonce"])
    has_create_attempt = _has_matching_template_create_attempt(records, intent)
    oid_witnesses = [
        record for record in records
        if record.get("event") in {
            "template_database_created", "template_database_renamed", "template_created",
        }
        and record.get("name") == receipt["name"]
        and record.get("oid") == receipt["oid"]
    ]
    if not has_create_attempt or not oid_witnesses:
        raise TemplateReceiptInvalid("template build attempt records do not corroborate the final receipt")


def _template_namespace_from_name(name):
    match = re.fullmatch(r"test_intevia_tpl_(me|vr)_[a-z0-9]{1,8}_[0-9a-f]{32}", str(name))
    if match is None:
        raise TemplateReceiptInvalid("template receipt name has no recognized namespace")
    return match.group(1)


def _listing_value(listing, key):
    prefix = key + "="
    values = [line[len(prefix):] for line in listing.splitlines() if line.startswith(prefix)]
    if len(values) != 1:
        raise TemplateReceiptInvalid("identity listing must contain exactly one %s line" % key)
    return values[0]


def _write_template_receipt(path, receipt):
    validate_template_receipt(receipt, receipt["identity"])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = str(path) + ".tmp." + receipt["run_nonce"]
    with open(temporary, "x", encoding="utf-8", newline="\n") as receipt_file:
        json.dump(receipt, receipt_file, ensure_ascii=True, separators=(",", ":"))
        receipt_file.write("\n")
        receipt_file.flush()
        os.fsync(receipt_file.fileno())
    if path.exists():
        raise TemplateNameCollision("template receipt appeared before atomic publication")
    os.replace(temporary, path)


def _write_build_intent(path, intent):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = str(path) + ".tmp." + intent["run_nonce"]
    with open(temporary, "w", encoding="utf-8", newline="\n") as intent_file:
        json.dump(intent, intent_file, ensure_ascii=True, separators=(",", ":"))
        intent_file.write("\n")
        intent_file.flush()
        os.fsync(intent_file.fileno())
    os.replace(temporary, path)


def _set_build_stage(path, intent, stage, transition_hook=None):
    intent["stage"] = stage
    _write_build_intent(path, intent)
    if transition_hook is not None:
        transition_hook(stage, dict(intent))


def _read_attempt_records(path, nonce):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TemplateReceiptInvalid("build attempt records cannot be read") from exc
    records = []
    for number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TemplateReceiptInvalid("build attempt record %d is not JSON" % number) from exc
        if not isinstance(record, dict) or record.get("run_nonce") != nonce:
            raise TemplateReceiptInvalid("build attempt record belongs to a different run nonce")
        records.append(record)
    return records


def _has_matching_template_create_attempt(records, intent):
    matching = [
        record for record in records
        if isinstance(record, dict)
        and record.get("event") == "template_create_attempt"
        and record.get("name") == intent.get("bootstrap_name")
        and record.get("unit_tag") == intent.get("database_tag")
        and record.get("evidence_root") == intent.get("evidence_root")
        and record.get("run_nonce") == intent.get("run_nonce")
    ]
    if not matching or any(record.get("template") is not None for record in matching):
        return False
    stable_records = {
        json.dumps(
            {key: value for key, value in record.items() if key not in {"at", "pid"}},
            sort_keys=True,
            separators=(",", ":"),
        )
        for record in matching
    }
    return len(stable_records) == 1


def _path_is_within(path, root):
    if not isinstance(path, (str, os.PathLike)) or not path:
        return False
    try:
        resolved_path = str(Path(path).resolve())
        resolved_root = str(Path(root).resolve())
        return os.path.normcase(os.path.commonpath([resolved_path, resolved_root])) == os.path.normcase(
            resolved_root
        )
    except (OSError, TypeError, ValueError):
        return False


def _validate_receipt_store(receipt, receipt_path):
    intent_path = Path(receipt["build_intent"]["path"]).resolve()
    if intent_path.parent.name != "template-build-intents":
        raise TemplateReceiptInvalid("template build intent is not in its owning store")
    owning_store = intent_path.parent.parent
    resolved_receipt = Path(receipt_path).resolve()
    if (
        resolved_receipt.name != receipt["name"] + ".json"
        or resolved_receipt.parent != owning_store
    ):
        raise TemplateReceiptInvalid("template receipt is not in its owning store")


def _resolve_build_intent(intent_path, connection, evidence_root_path):
    try:
        intent = json.loads(Path(intent_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TemplateReceiptInvalid("template build intent cannot be read") from exc
    if (
        not isinstance(intent, dict)
        or type(intent.get("version")) is not int
        or intent.get("version") != 1
        or intent.get("database_tag") != _required_database_tag()
        or intent.get("evidence_root") != _required_evidence_root()
        or not isinstance(intent.get("run_nonce"), str)
        or re.fullmatch(r"[0-9a-f]{32}", intent["run_nonce"]) is None
    ):
        raise TemplateReceiptInvalid("template build intent does not belong to this unit")
    nonce = intent["run_nonce"]
    database_tag = _required_database_tag()
    namespace = intent.get("namespace")
    if namespace not in {"me", "vr"}:
        raise TemplateReceiptInvalid("template build intent has an invalid namespace")
    if intent.get("bootstrap_name") != "test_intevia_boot_%s_%s" % (database_tag, nonce):
        raise TemplateReceiptInvalid("template build bootstrap name is not derived from its unit nonce")
    if not isinstance(intent.get("candidate_binding"), str) or re.fullmatch(
        r"[0-9a-f]{64}", intent["candidate_binding"]
    ) is None:
        raise TemplateReceiptInvalid("template build intent has no valid candidate binding")
    if not _path_is_within(intent.get("attempt_receipt_path", ""), evidence_root_path):
        raise TemplateReceiptInvalid("template build attempt records are outside the authorized evidence root")
    if intent.get("target_name") is None:
        if intent.get("identity") is not None or intent.get("receipt_path") is not None:
            raise TemplateReceiptInvalid("template build intent has a partial target identity")
    elif (
        not isinstance(intent.get("identity"), str)
        or re.fullmatch(r"[0-9a-f]{64}", intent["identity"]) is None
        or intent.get("target_name") != _template_database_name(
            intent["identity"], namespace, database_tag
        )
        or not _path_is_within(intent.get("receipt_path", ""), evidence_root_path)
    ):
        raise TemplateReceiptInvalid("template build target is not bound to its full identity and evidence root")
    if intent.get("database_oid") is not None and (
        type(intent["database_oid"]) is not int or intent["database_oid"] <= 0
    ):
        raise TemplateReceiptInvalid("template build intent has an invalid database OID")
    if intent.get("stage") not in {
        "INTENT_WRITTEN", "BOOTSTRAP_CREATED", "MIGRATED", "RENAME_PENDING",
        "RENAMED", "SEALED", "TEMPLATE_FLAGGED", "TEMPLATE_FLAG_UNAVAILABLE",
        "FINAL_RECEIPT", "ATTEMPT_RECEIPT", "COMPLETE", "RECOVERED_ABSENT",
    }:
        raise TemplateReceiptInvalid("template build intent has no lifecycle stage")
    if intent.get("stage") in {"COMPLETE", "RECOVERED_ABSENT"}:
        return intent

    names = [intent["bootstrap_name"]]
    if intent.get("target_name"):
        names.append(intent["target_name"])
    databases = [(name, _database_record(connection, name)) for name in names]
    present = [(name, database) for name, database in databases if database is not None]
    if not present:
        intent["stage"] = "RECOVERED_ABSENT"
        _write_build_intent(intent_path, intent)
        return intent

    records = _read_attempt_records(intent["attempt_receipt_path"], nonce)
    if not _has_matching_template_create_attempt(records, intent):
        raise TemplateReceiptInvalid("template build intent has no matching create-attempt witness")

    if len(present) != 1:
        bootstrap = next((database for name, database in present if name == intent["bootstrap_name"]), None)
        target = next((database for name, database in present if name == intent.get("target_name")), None)
        receipt_path = intent.get("receipt_path")
        if bootstrap is None or target is None or not receipt_path or not Path(receipt_path).is_file():
            raise TemplateReceiptInvalid("more than one catalog database matches a template build intent")
        receipt = read_template_receipt(receipt_path, intent.get("identity"), namespace)
        if receipt is None or receipt["name"] != intent["target_name"] or receipt["oid"] != target["oid"]:
            raise TemplateReceiptInvalid("existing target database has no matching final receipt")
        verify_registered_template(connection, receipt, intent["identity"], receipt["listing"], namespace)
        if intent.get("database_oid") is not None and intent["database_oid"] != bootstrap["oid"]:
            raise TemplateReceiptInvalid("bootstrap OID differs from the interrupted reuse intent")
        if _drop_database_by_oid(
            connection, intent["bootstrap_name"], bootstrap["oid"], clear_is_template=True
        ) is not True:
            raise TemplateReceiptInvalid("interrupted reuse bootstrap cleanup was not confirmed")
        intent["stage"] = "COMPLETE"
        _write_build_intent(intent_path, intent)
        _attempt_receipt(
            "template_bootstrap_destroyed",
            name=intent["bootstrap_name"],
            oid=bootstrap["oid"],
            confirmed_absent=True,
        )
        return intent

    name, database = present[0]
    oid = database["oid"]
    receipt_path = intent.get("receipt_path")
    receipt = None
    if name == intent.get("target_name") and receipt_path and Path(receipt_path).is_file():
        receipt = read_template_receipt(
            receipt_path, intent.get("identity"), namespace
        )
        if receipt is not None:
            receipt_intent_is_current = (
                Path(receipt["build_intent"]["path"]).resolve() == Path(intent_path).resolve()
            )
            if (
                receipt["name"] != name
                or receipt["oid"] != oid
                or (receipt_intent_is_current and (
                    receipt["run_nonce"] != intent["run_nonce"]
                    or receipt.get("candidate_binding") != intent["candidate_binding"]
                ))
            ):
                raise TemplateReceiptInvalid("final template receipt does not match the catalog database")
            _validate_receipt_store(receipt, receipt_path)
            verify_registered_template(
                connection, receipt, intent.get("identity"), receipt["listing"], namespace
            )
            return intent

    if intent.get("database_oid") is not None and int(intent["database_oid"]) != oid:
        raise TemplateReceiptInvalid("template build catalog OID differs from its intent")
    witnessed_oids = []
    for record in records:
        if record.get("event") in {
            "template_database_created", "template_database_renamed",
        } and record.get("name") in names:
            if type(record.get("oid")) is not int or record["oid"] <= 0:
                raise TemplateReceiptInvalid("template build attempt contains an invalid catalog OID")
            witnessed_oids.append(record["oid"])
    if any(witnessed_oid != oid for witnessed_oid in witnessed_oids):
        raise TemplateReceiptInvalid("template build attempt OID differs from the catalog")

    # TU1-T2: recovery drops only the catalog OID corroborated by this unit's intent and attempt witness.
    absent = _drop_database_by_oid(connection, name, oid, clear_is_template=True)
    if not absent:
        raise TemplateReceiptInvalid("recovery could not confirm absence of the owned template build")
    intent["stage"] = "RECOVERED_ABSENT"
    _write_build_intent(intent_path, intent)
    _attempt_receipt(
        "template_build_recovered",
        name=name,
        oid=oid,
        recovery="OWNED_INCOMPLETE_DATABASE_REMOVED",
        source_run_nonce=intent["run_nonce"],
        confirmed_absent=True,
    )
    return intent


def recover_template_builds(connection, evidence_root_path):
    root = Path(evidence_root_path).resolve()
    if not root.is_dir():
        raise TemplateReceiptInvalid("authorized evidence root is absent during build recovery")
    expected_root = _required_evidence_root()
    if root.name != expected_root:
        raise TemplateReceiptInvalid("build recovery evidence root does not match the authorized unit")
    intents = sorted(root.glob("**/template-build-intents/*.json"))
    if any(not _path_is_within(path, root) for path in intents):
        raise TemplateReceiptInvalid("template build intent path is outside the authorized evidence root")
    return [_resolve_build_intent(path, connection, root) for path in intents]


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
    _attempt_receipt(
        "template_create_attempt",
        name=name,
        template=template_name,
        unit_tag=_required_database_tag(),
        evidence_root=_required_evidence_root(),
        run_nonce=os.environ["VERIFICATION_RUN_NONCE"],
    )
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
    from django.db import DatabaseError

    record = _database_record(connection, name)
    if record is None:
        return True
    if record["oid"] != oid:
        raise TemplateReceiptInvalid("refusing to drop %s: current OID differs from its receipt" % name)
    quote = connection.ops.quote_name
    with connection.creation._nodb_cursor() as cursor:
        if record["allow_connections"]:
            cursor.execute("ALTER DATABASE %s WITH ALLOW_CONNECTIONS false" % quote(name))
    current = _database_record(connection, name)
    if current is None or current["oid"] != oid:
        raise TemplateReceiptInvalid("database changed while cleanup was closing access")
    if current["active_connections"]:
        if record["allow_connections"]:
            with connection.creation._nodb_cursor() as cursor:
                cursor.execute("ALTER DATABASE %s WITH ALLOW_CONNECTIONS true" % quote(name))
        raise TemplateReceiptInvalid("refusing to remove a database with active sessions")

    def restore_original_flags():
        with connection.creation._nodb_cursor() as cursor:
            if clear_is_template and record["is_template"]:
                cursor.execute("ALTER DATABASE %s WITH IS_TEMPLATE true" % quote(name))
            if record["allow_connections"]:
                cursor.execute("ALTER DATABASE %s WITH ALLOW_CONNECTIONS true" % quote(name))

    if clear_is_template and record["is_template"]:
        with connection.creation._nodb_cursor() as cursor:
            cursor.execute("ALTER DATABASE %s WITH IS_TEMPLATE false" % quote(name))
    try:
        with connection.creation._nodb_cursor() as cursor:
            cursor.execute("DROP DATABASE %s" % quote(name))
    except DatabaseError as exc:
        restore_original_flags()
        raise TemplateReceiptInvalid("database is still in use; cleanup refused") from exc
    if _database_record(connection, name) is None:
        return True
    restore_original_flags()
    return False


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
    control_name = "test_intevia_ctl_" + _required_database_tag() + "_" + uuid.uuid4().hex[:20]
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


def _build_template(connection, alias, store, source_lines, transition_hook=None):
    from django.conf import settings

    nonce = os.environ["VERIFICATION_RUN_NONCE"]
    if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise TemplateIdentityUnavailable("VERIFICATION_RUN_NONCE must be a 32-character lowercase hex value")
    database_tag = _required_database_tag()
    evidence_root = _required_evidence_root()
    candidate_binding = _required_candidate_binding()
    original_connection_name = connection.settings_dict["NAME"]
    original_settings_name = settings.DATABASES[alias]["NAME"]
    bootstrap_name = "test_intevia_boot_" + database_tag + "_" + nonce
    attempt_receipt_path = os.environ.get("VERIFICATION_DB_RECEIPT")
    if not attempt_receipt_path:
        raise TemplateIdentityUnavailable("attempt receipt path is required before a template build")
    intent_path = Path(store) / "template-build-intents" / (nonce + ".json")
    intent = {
        "version": 1,
        "namespace": _required_template_namespace(),
        "database_tag": database_tag,
        "evidence_root": evidence_root,
        "run_nonce": nonce,
        "attempt_receipt_path": attempt_receipt_path,
        "bootstrap_name": bootstrap_name,
        "database_oid": None,
        "target_name": None,
        "identity": None,
        "receipt_path": None,
        "candidate_binding": candidate_binding,
        "stage": "INTENT_WRITTEN",
    }
    # TU1-T2: persist the unit and nonce before creating the bootstrap database.
    _write_build_intent(intent_path, intent)
    _attempt_receipt(
        "template_build_intent",
        name=bootstrap_name,
        unit_tag=database_tag,
        evidence_root=evidence_root,
        intent=str(intent_path),
        run_nonce=nonce,
    )
    if transition_hook is not None:
        transition_hook("INTENT_WRITTEN", dict(intent))

    bootstrap = _create_database(connection, bootstrap_name)
    intent["database_oid"] = bootstrap["oid"]
    _set_build_stage(intent_path, intent, "BOOTSTRAP_CREATED", transition_hook)

    _, cp0_digest, runtime_lines = _migrate_build_database(connection, alias, bootstrap_name)
    listing, identity = complete_identity(source_lines + runtime_lines)
    target_name = template_database_name(identity)
    target_path = template_receipt_path(store, identity)
    intent.update(identity=identity, target_name=target_name, receipt_path=str(target_path))
    _set_build_stage(intent_path, intent, "MIGRATED", transition_hook)

    existing_database = _database_record(connection, target_name)
    existing_receipt = read_template_receipt(target_path)
    if existing_database is not None:
        if existing_receipt is None:
            raise TemplateNameCollision("bounded template name exists without an identity receipt")
        _validate_receipt_store(existing_receipt, target_path)
        validate_template_receipt(existing_receipt, identity)
        verify_registered_template(connection, existing_receipt, identity, listing)
        if not _drop_database_by_oid(connection, bootstrap_name, bootstrap["oid"]):
            raise TemplateReceiptInvalid("temporary build database cleanup was not confirmed")
        _attempt_receipt("template_bootstrap_destroyed", name=bootstrap_name, oid=bootstrap["oid"],
                         confirmed_absent=True)
        _set_build_stage(intent_path, intent, "COMPLETE", transition_hook)
        return existing_receipt, False
    if existing_receipt is not None:
        raise TemplateNameCollision("template receipt exists but its database is absent")

    quote = connection.ops.quote_name
    _set_build_stage(intent_path, intent, "RENAME_PENDING", transition_hook)
    with connection.creation._nodb_cursor() as cursor:
        cursor.execute("ALTER DATABASE %s RENAME TO %s" % (quote(bootstrap_name), quote(target_name)))
    _attempt_receipt(
        "template_database_renamed",
        name=target_name,
        previous_name=bootstrap_name,
        oid=bootstrap["oid"],
        unit_tag=database_tag,
        evidence_root=evidence_root,
    )
    _set_build_stage(intent_path, intent, "RENAMED", transition_hook)

    try:
        with connection.creation._nodb_cursor() as cursor:
            cursor.execute("ALTER DATABASE %s WITH ALLOW_CONNECTIONS false" % quote(target_name))
    except Exception:
        raise TemplateSealingRefused("role could not set required ALLOW_CONNECTIONS false seal") from None
    _attempt_receipt("template_database_sealed", name=target_name, oid=bootstrap["oid"],
                     unit_tag=database_tag, evidence_root=evidence_root)
    _set_build_stage(intent_path, intent, "SEALED", transition_hook)

    is_template = False
    try:
        with connection.creation._nodb_cursor() as cursor:
            cursor.execute("ALTER DATABASE %s WITH IS_TEMPLATE true" % quote(target_name))
        is_template = True
    except Exception as exc:
        _attempt_receipt("template_is_template_unavailable", name=target_name,
                         error_type=type(exc).__name__)
    _set_build_stage(intent_path, intent, "TEMPLATE_FLAGGED" if is_template else "TEMPLATE_FLAG_UNAVAILABLE",
                     transition_hook)

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
        "build_intent": {
            "path": str(intent_path),
            "identity": identity,
            "run_nonce": nonce,
            "database_oid": bootstrap["oid"],
        },
        "database_tag": database_tag,
        "evidence_root": evidence_root,
        "candidate_binding": candidate_binding,
        "cp0_digest": cp0_digest,
        "fresh_build_cp0_digest": cp0_digest,
        "allow_connections": False,
        "is_template": is_template,
    }
    receipt.update(refusal_witness)
    _write_template_receipt(target_path, receipt)
    _set_build_stage(intent_path, intent, "FINAL_RECEIPT", transition_hook)
    _attempt_receipt("template_created", name=target_name, oid=database["oid"], identity=identity,
                     receipt=str(target_path), cp0_digest=cp0_digest,
                     allow_connections=False, is_template=is_template,
                     connection_refused=refusal_witness["connection_refused"],
                     catalog_datallowconn=refusal_witness["connection_refusal_catalog_datallowconn"],
                     control_connected=refusal_witness["connection_refusal_control_connected"],
                     unit_tag=database_tag, evidence_root=evidence_root)
    return receipt, True


def prepare_template(connection, alias, store):
    _required_template_namespace()
    _required_candidate_binding()
    evidence_root_path = os.environ.get("INTEVIA_EVIDENCE_ROOT_PATH")
    if not evidence_root_path:
        raise TemplateIdentityUnavailable("authorized evidence root path is required for template recovery")
    recover_template_builds(connection, evidence_root_path)
    source_lines = source_input_lines()
    expected_listing, expected_identity = complete_identity(source_lines + runtime_input_lines(connection))
    path = template_receipt_path(store, expected_identity)
    Path(store).mkdir(parents=True, exist_ok=True)
    receipt = read_template_receipt(path, expected_identity)
    if receipt is not None and _database_record(connection, receipt["name"]) is None:
        _archive_absent_template_receipt(connection, path, receipt, expected_identity)
        receipt = None
    if receipt is not None:
        _validate_receipt_store(receipt, path)
        verify_registered_template(connection, receipt, expected_identity, expected_listing)
        return receipt, False
    if _database_record(connection, template_database_name(expected_identity)) is not None:
        raise TemplateNameCollision("bounded template name exists without a matching full-identity receipt")
    try:
        return _build_template(connection, alias, store, source_lines)
    except Exception:
        recover_template_builds(connection, evidence_root_path)
        raise


def create_clone_database(connection, template_receipt, test_database_name):
    from verification.ownership import CollisionRefused

    _required_candidate_binding()
    store = os.environ.get("VERIFICATION_TEMPLATE_STORE")
    if not store:
        raise TemplateReceiptInvalid("template store is required to clone its owned database")
    owning_store = Path(template_receipt["build_intent"]["path"]).resolve().parent.parent
    if not _path_is_within(str(owning_store), Path(store).resolve()):
        raise TemplateReceiptInvalid("template receipt is outside the current owning store")
    _validate_receipt_store(
        template_receipt, template_receipt_path(owning_store, template_receipt["identity"])
    )
    listing, identity = collect_template_identity(connection=connection)
    if identity != template_receipt["identity"] or listing != template_receipt["listing"]:
        raise TemplateIdentityUnavailable("current inputs differ from the recorded template identity")
    verify_registered_template(connection, template_receipt, identity, listing)
    protected = {"intevia", "intevia_dev", os.environ.get("INTEVIA_POSTGRES_DB")}
    if test_database_name in protected:
        raise CollisionRefused("protected database name refused for clone")
    existing = _database_record(connection, test_database_name)
    if existing is not None:
        # CI-CLONE-1: the same collision_refused receipt as the owned fresh path (verification.ownership.install), so the
        # route's OWNERSHIP collision check reads the refusal and the untouched database's oid in clone mode too.
        _attempt_receipt("collision_refused", name=test_database_name, existing_oid=existing["oid"])
        raise CollisionRefused("clone database %s already exists (oid %s); refused without dropping it"
                               % (test_database_name, existing["oid"]))

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
    _required_candidate_binding()
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


def _write_cleanup_store_record(receipt_path, record):
    directory = Path(receipt_path).parent / "template-cleanup-records"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (record["cleanup_run_nonce"] + ".json")
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TemplateReceiptInvalid("existing store cleanup record cannot be read") from exc
        if (
            not isinstance(existing, dict)
            or {key: value for key, value in existing.items() if key != "database_absent_before_cleanup"}
            != {key: value for key, value in record.items() if key != "database_absent_before_cleanup"}
        ):
            raise TemplateReceiptInvalid("cleanup run nonce is already bound to a different store record")
        return destination

    temporary = str(destination) + ".tmp." + uuid.uuid4().hex
    with open(temporary, "x", encoding="utf-8", newline="\n") as record_file:
        json.dump(record, record_file, ensure_ascii=True, separators=(",", ":"))
        record_file.write("\n")
        record_file.flush()
        os.fsync(record_file.fileno())
    try:
        os.link(temporary, destination)
    except FileExistsError:
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TemplateReceiptInvalid("concurrent store cleanup record cannot be read") from exc
        if (
            not isinstance(existing, dict)
            or {key: value for key, value in existing.items() if key != "database_absent_before_cleanup"}
            != {key: value for key, value in record.items() if key != "database_absent_before_cleanup"}
        ):
            raise TemplateReceiptInvalid("cleanup run nonce is already bound to a different store record")
    os.unlink(temporary)
    return destination


def cleanup_template(connection, receipt_path, identity, allow_incomplete_parity=False):
    receipt = read_template_receipt(receipt_path, identity)
    if receipt is None:
        raise TemplateReceiptInvalid("template receipt is absent during cleanup")
    validate_template_receipt(receipt, identity)
    store = os.environ.get("VERIFICATION_TEMPLATE_STORE")
    namespace = _required_template_namespace()
    if (
        not store
        or Path(receipt_path).name != template_database_name(identity) + ".json"
        or not _path_is_within(str(Path(receipt_path).resolve()), Path(store).resolve())
    ):
        raise TemplateReceiptInvalid("template receipt is outside the current owning store")
    _validate_receipt_store(receipt, receipt_path)
    persisted = read_template_receipt(receipt_path, identity)
    if persisted != receipt:
        raise TemplateReceiptInvalid("template receipt changed before cleanup")
    destroyed = receipt.get("destroyed")
    if destroyed is not None:
        if (
            not isinstance(destroyed, dict)
            or destroyed.get("oid") != receipt["oid"]
            or destroyed.get("run_nonce") != receipt["run_nonce"]
            or destroyed.get("confirmed_absent") is not True
            or not isinstance(destroyed.get("cleanup_run_nonce"), str)
            or re.fullmatch(r"[0-9a-f]{32}", destroyed["cleanup_run_nonce"]) is None
            or not _path_is_within(destroyed.get("cleanup_record"), store)
        ):
            raise TemplateReceiptInvalid("consumed template receipt has invalid cleanup evidence")
        try:
            cleanup_record = json.loads(
                Path(destroyed["cleanup_record"]).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TemplateReceiptInvalid("consumed template store record cannot be read") from exc
        if (
            not isinstance(cleanup_record, dict)
            or cleanup_record.get("event") != "template_destroyed"
            or cleanup_record.get("identity") != identity
            or cleanup_record.get("name") != receipt["name"]
            or cleanup_record.get("oid") != receipt["oid"]
            or cleanup_record.get("created_nonce") != receipt["run_nonce"]
            or cleanup_record.get("cleanup_run_nonce") != destroyed["cleanup_run_nonce"]
            or cleanup_record.get("candidate_binding") is None
            or cleanup_record.get("confirmed_absent") is not True
        ):
            raise TemplateReceiptInvalid("consumed template store record does not match its receipt")
        if _database_record(connection, receipt["name"]) is None:
            return True
        raise TemplateReceiptInvalid("consumed template database name is occupied")
    _verify_template_build_intent(receipt)
    if not receipt.get("connection_refused") or not receipt.get("allow_connections") is False:
        raise TemplateReceiptInvalid("template seal and direct-connection witness are incomplete")
    final_digest = receipt.get("final_clone_cp0_digest")
    if final_digest is not None and final_digest != receipt.get("cp0_digest"):
        raise TemplateReceiptInvalid("final clone CP-0 does not match the template's recorded digest")
    if final_digest is None and (namespace != "me" or not allow_incomplete_parity):
        raise TemplateReceiptInvalid("final clone CP-0 has not been recorded")
    cleanup_run_nonce = os.environ.get("VERIFICATION_RUN_NONCE")
    if not isinstance(cleanup_run_nonce, str) or re.fullmatch(r"[0-9a-f]{32}", cleanup_run_nonce) is None:
        raise TemplateIdentityUnavailable("cleanup run nonce is invalid")
    cleanup_candidate_binding = os.environ.get("INTEVIA_CANDIDATE_BINDING_SHA256")
    if not isinstance(cleanup_candidate_binding, str) or re.fullmatch(
        r"[0-9a-f]{64}", cleanup_candidate_binding
    ) is None:
        raise TemplateIdentityUnavailable("cleanup candidate binding is invalid")

    database = _database_record(connection, receipt["name"])
    if database is not None and (
        database["oid"] != receipt["oid"] or database["allow_connections"]
    ):
        raise TemplateReceiptInvalid("template OID or sealed state differs from its ownership receipt")
    # TU1-T3: stop new sessions before checking existing sessions; only then can the owned OID be dropped.
    _attempt_receipt("template_destroy_attempt", name=receipt["name"], oid=receipt["oid"],
                     created_nonce=receipt["run_nonce"], identity=identity)
    if database is not None:
        absent = _drop_database_by_oid(connection, receipt["name"], receipt["oid"], clear_is_template=True)
        if not absent:
            raise TemplateReceiptInvalid("template absence could not be confirmed")
    elif _database_record(connection, receipt["name"]) is not None:
        raise TemplateReceiptInvalid("template absence could not be confirmed")

    cleanup_record = {
        "event": "template_destroyed",
        "identity": identity,
        "name": receipt["name"],
        "oid": receipt["oid"],
        "created_nonce": receipt["run_nonce"],
        "cleanup_run_nonce": cleanup_run_nonce,
        "candidate_binding": cleanup_candidate_binding,
        "confirmed_absent": True,
        "database_absent_before_cleanup": database is None,
    }
    cleanup_record_path = _write_cleanup_store_record(receipt_path, cleanup_record)
    receipt["destroyed"] = {
        "oid": receipt["oid"],
        "run_nonce": receipt["run_nonce"],
        "cleanup_run_nonce": cleanup_run_nonce,
        "cleanup_record": str(cleanup_record_path),
        "confirmed_absent": True,
    }
    temporary = str(receipt_path) + ".tmp." + uuid.uuid4().hex
    with open(temporary, "x", encoding="utf-8", newline="\n") as receipt_file:
        json.dump(receipt, receipt_file, ensure_ascii=True, separators=(",", ":"))
        receipt_file.write("\n")
        receipt_file.flush()
        os.fsync(receipt_file.fileno())
    os.replace(temporary, receipt_path)
    _attempt_receipt("template_destroyed", name=receipt["name"], oid=receipt["oid"],
                     created_nonce=receipt["run_nonce"], confirmed_absent=True)
    return True