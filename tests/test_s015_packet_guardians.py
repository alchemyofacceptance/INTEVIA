"""PKT-A authorized-path boundary guardian with commissioned later-slice exceptions."""

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path

from django.test import SimpleTestCase


class S015PacketBoundaryTests(SimpleTestCase):
    def test_authorized_packet_a_paths_exist_and_forbidden_services_do_not(self):
        root = Path(__file__).resolve().parents[1]
        authorized = {
            "core/models.py",
            "core/organism_contract.py",
            "core/migrations/0019_s015_living_organism_foundation.py",
            "src/intevia/services/__init__.py",
            "src/intevia/services/organism_authority.py",
            "src/intevia/services/organism_membership_service.py",
            "tests/test_s015_models.py",
            "tests/test_s015_migrations.py",
            "tests/test_s015_authority.py",
            "tests/test_s015_membership_service.py",
            "tests/test_s015_postgresql_guardians.py",
            "tests/test_s015_packet_guardians.py",
            "src/intevia/services/organism_entitlement_service.py",
        }
        self.assertEqual(len(authorized), 13)
        deferred_se_service = "src/intevia/services/organism_entitlement_service.py"
        self.assertTrue(
            all(
                (root / path).is_file()
                for path in authorized
                if path != deferred_se_service
            )
        )
        self.assertIn(deferred_se_service, authorized)
        # SB-G already authorizes this implemented service; SE's service arrives in S2.
        self.assertTrue(
            (root / "src/intevia/services/organism_governance_service.py").is_file()
        )
        self.assertFalse((root / "src/intevia/services/organism_queries.py").exists())


_RAW_RULES = {
    "P1": re.compile(r"\bsetval\s*\(", re.IGNORECASE),
    "P2": re.compile(r"\bRESTART\s+IDENTITY\b", re.IGNORECASE),
    "P3": re.compile(r"\bALTER\s+SEQUENCE\b", re.IGNORECASE),
}
_INSERT_WITH_ID = re.compile(
    r"\bINSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_$.\"`]*)\s*\(([^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
_ADVISORY_RULES = (
    (
        "D2-RESET",
        "reset-sequences",
        re.compile(r"\breset_sequences\s*=\s*True\b", re.IGNORECASE),
        "reset_sequences declaration",
    ),
    (
        "D2-SERIALIZED",
        "f",
        re.compile(r"\bserialized_rollback\s*=\s*True\b", re.IGNORECASE),
        "serialized_rollback declaration",
    ),
    (
        "D2-FIXTURE",
        "e",
        re.compile(r"^\s*fixtures\s*=", re.IGNORECASE | re.MULTILINE),
        "fixtures declaration",
    ),
    (
        "D2-ASSERT-ID",
        "literal-id-assertion",
        re.compile(
            r"\bassert(?:Equal|In|Is)\s*\([^;\r\n]*\.(?:pk|id)\b"
            r"[^;\r\n]*,\s*-?\d+\b",
            re.IGNORECASE,
        ),
        "assertion compares an id with a literal",
    ),
    (
        "D2-DESERIALIZE",
        "serialized-restore",
        re.compile(r"\bdeserialize_db_from_string\b"),
        "deserialize_db_from_string reference",
    ),
)
_SCAN_ROOTS = ("core", "intevia", "src", "tests", "verification", "scripts")
_ALLOWLIST = (
    (
        "tests/test_s015_packet_guardians.py",
        "P1",
        "the guardian contains its synthetic refusal fixtures",
    ),
    (
        "tests/test_s015_packet_guardians.py",
        "P2",
        "the guardian contains its synthetic refusal fixtures",
    ),
    (
        "tests/test_s015_packet_guardians.py",
        "P3",
        "the guardian contains its synthetic refusal fixtures",
    ),
    (
        "tests/test_s015_packet_guardians.py",
        "P4",
        "the guardian contains its synthetic refusal fixtures",
    ),
    (
        "tests/test_s015_packet_guardians.py",
        "P5",
        "the guardian contains its synthetic refusal fixtures",
    ),
    (
        "intevia/test_postgresql_backend/operations.py",
        "P1",
        "the backend implements the counter-aware sequence reset",
    ),
    (
        "tests/test_s015_test_harness.py",
        "P1",
        "H64 sequence witnesses deliberately wind counters and restore them through sequence_reset_sql",
    ),
    (
        "tests/test_library_exact_version_contract.py",
        "P5",
        "deliberate wide-id precision fixture; TestCase rolls back the row and the test asserts exact 9007199254740993",
    ),
)


@dataclass(frozen=True)
class SequenceFinding:
    path: str
    line: int
    rule: str
    finding_class: str
    text: str

    def render(self):
        return (
            f"{self.path}:{self.line} | {self.rule} | "
            f"{self.finding_class} | {self.text}"
        )


def _line_number(source, offset):
    return source.count("\n", 0, offset) + 1


def _is_literal(node):
    try:
        ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError):
        return False
    return True


def _docstring_nodes(tree):
    result = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            result.add(id(body[0].value))
    return result


def _model_names(tree):
    model_names = set()
    model_module_names = {"models"}
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                bound = alias.asname or alias.name
                if module == "django.db" and alias.name == "models":
                    model_module_names.add(bound)
                elif module == "django.db.models" and alias.name == "Model":
                    model_names.add(bound)
                elif (
                    module.endswith(".models")
                    and module != "django.db.models"
                    and alias.name != "*"
                ):
                    model_names.add(bound)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "django.db.models":
                    model_module_names.add(alias.asname or "models")

    def is_model_base(base):
        if isinstance(base, ast.Name):
            return base.id in model_names
        return (
            isinstance(base, ast.Attribute)
            and base.attr == "Model"
            and isinstance(base.value, ast.Name)
            and base.value.id in model_module_names
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(is_model_base(base) for base in node.bases):
            model_names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None:
                continue
            is_lookup = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "get_model"
            )
            is_alias = isinstance(value, ast.Name) and value.id in model_names
            is_base = is_model_base(value)
            if is_lookup or is_alias or is_base:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                model_names.update(
                    target.id for target in targets if isinstance(target, ast.Name)
                )
    return model_names, parents


def _bulk_create_ancestor(node, parents):
    current = parents.get(node)
    while current is not None:
        if (
            isinstance(current, ast.Call)
            and isinstance(current.func, ast.Attribute)
            and current.func.attr == "bulk_create"
        ):
            return True
        current = parents.get(current)
    return False


def _expression_key(node):
    if isinstance(node, ast.Name):
        return ("name", node.id)
    if isinstance(node, ast.Attribute):
        return ("attribute", _expression_key(node.value), node.attr)
    return None


def _model_constructor_persistence(tree, model_names, parents):
    persisted = {}
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        scoped = list(_scope_nodes(function))
        assignments = []
        saves = []
        bulk_creates = []
        for node in scoped:
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                targets = []
                value = None
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in model_names
            ):
                assignments.extend(
                    (target, value)
                    for target in targets
                    if isinstance(target, (ast.Name, ast.Attribute))
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "save"
            ):
                saves.append(node)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "bulk_create"
            ):
                bulk_creates.append(node)

        for target, constructor in assignments:
            target_key = _expression_key(target)
            for bulk_create in bulk_creates:
                if bulk_create.lineno <= constructor.lineno:
                    continue
                if any(
                    _expression_key(candidate) == target_key
                    for argument in bulk_create.args
                    for candidate in ast.walk(argument)
                ):
                    persisted[id(constructor)] = ("P5b", bulk_create)
                    break
            if id(constructor) in persisted:
                continue
            for save in saves:
                receiver = save.func.value
                if (
                    save.lineno > constructor.lineno
                    and _expression_key(receiver) == target_key
                    and not any(
                        isinstance(node, (ast.Assign, ast.AnnAssign))
                        and node.lineno > constructor.lineno
                        and node.lineno < save.lineno
                        and any(
                            _expression_key(target) == target_key
                            for target in (
                                node.targets
                                if isinstance(node, ast.Assign)
                                else [node.target]
                            )
                        )
                        for node in scoped
                    )
                ):
                    persisted[id(constructor)] = ("P5a", save)
                    break

    for constructor in ast.walk(tree):
        if not (
            isinstance(constructor, ast.Call)
            and isinstance(constructor.func, ast.Name)
            and constructor.func.id in model_names
        ):
            continue
        parent = parents.get(constructor)
        if (
            isinstance(parent, ast.Attribute)
            and parent.attr == "save"
            and parent.value is constructor
        ):
            save_call = parents.get(parent)
            if isinstance(save_call, ast.Call) and save_call.func is parent:
                persisted[id(constructor)] = ("P5a", save_call)
    return persisted


def _mocked_model_write(node, model_name, parents):
    if not model_name:
        return False
    current = node
    while current is not None:
        if isinstance(current, (ast.With, ast.AsyncWith)):
            for item in current.items:
                context = item.context_expr
                if not (
                    isinstance(context, ast.Call)
                    and isinstance(context.func, ast.Attribute)
                    and context.func.attr == "object"
                    and len(context.args) >= 2
                    and isinstance(context.args[0], ast.Name)
                    and context.args[0].id == model_name
                    and isinstance(context.args[1], ast.Constant)
                    and context.args[1].value in ("objects", "save")
                ):
                    continue
                return True
        current = parents.get(current)
    return False


def _manager_model_name(node, model_names):
    if not isinstance(node.func, ast.Attribute):
        return None
    manager = node.func.value
    if (
        isinstance(manager, ast.Attribute)
        and manager.attr == "objects"
        and isinstance(manager.value, ast.Name)
        and manager.value.id in model_names
    ):
        return manager.value.id
    return None


def _defaults_has_id(value):
    if isinstance(value, ast.Dict):
        return any(
            isinstance(key, ast.Constant)
            and key.value in ("id", "pk")
            for key in value.keys
        )
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id == "dict" and any(
            keyword.arg in ("id", "pk") for keyword in value.keywords
        )
    return False


def _scope_nodes(node):
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield from _scope_nodes(child)


def _scan_python(path, source):
    tree = ast.parse(source, filename=path)
    findings = []
    docstrings = _docstring_nodes(tree)
    model_names, parents = _model_names(tree)
    constructor_persistence = _model_constructor_persistence(
        tree, model_names, parents
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            for rule, pattern in _RAW_RULES.items():
                for match in pattern.finditer(node.value):
                    findings.append(
                        SequenceFinding(
                            path, node.lineno, rule, "counter-operation", match.group(0)
                        )
                    )
            for match in _INSERT_WITH_ID.finditer(node.value):
                if any(
                    column.strip().lower() == "id"
                    for column in match.group(2).split(",")
                ):
                    findings.append(
                        SequenceFinding(
                            path,
                            node.lineno,
                            "P4",
                            "a",
                            f"INSERT INTO {match.group(1)} (id)",
                        )
                    )

        if not isinstance(node, ast.Call):
            continue
        function_name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id
            if isinstance(node.func, ast.Name)
            else None
        )
        if function_name in ("get_or_create", "update_or_create"):
            for keyword in node.keywords:
                if keyword.arg == "defaults" and _defaults_has_id(keyword.value):
                    findings.append(
                        SequenceFinding(
                            path,
                            node.lineno,
                            "P5d",
                            "a",
                            f"{function_name}(defaults containing id or pk)",
                        )
                    )
                elif keyword.arg in ("id", "pk") or keyword.arg is None:
                    findings.append(
                        SequenceFinding(
                            path,
                            node.lineno,
                            "A-PRIME",
                            "a-prime",
                            f"{function_name} conditional id write",
                        )
                    )
            continue

        model_constructor = (
            isinstance(node.func, ast.Name) and node.func.id in model_names
        )
        manager_write = function_name in ("create", "bulk_create")
        if not model_constructor and not manager_write:
            continue
        manager_model = _manager_model_name(node, model_names) if manager_write else None
        if manager_write and _mocked_model_write(node, manager_model, parents):
            continue
        for keyword in node.keywords:
            if keyword.arg in ("id", "pk"):
                if _is_literal(keyword.value):
                    if model_constructor and _bulk_create_ancestor(node, parents):
                        rule = "P5b"
                    elif model_constructor:
                        persistence = constructor_persistence.get(id(node))
                        if persistence is None:
                            continue
                        rule, write_call = persistence
                        if _mocked_model_write(write_call, node.func.id, parents):
                            continue
                    else:
                        rule = "P5a"
                    findings.append(
                        SequenceFinding(
                            path,
                            node.lineno,
                            rule,
                            "a",
                            f"{function_name or node.func.id}({keyword.arg}=<literal>)",
                        )
                    )
                else:
                    findings.append(
                        SequenceFinding(
                            path,
                            node.lineno,
                            "A-PRIME",
                            "a-prime",
                            f"{function_name or node.func.id}({keyword.arg}=<nonliteral>)",
                        )
                    )
            elif keyword.arg is None:
                findings.append(
                    SequenceFinding(
                        path,
                        node.lineno,
                        "A-PRIME",
                        "a-prime",
                        f"{function_name or node.func.id}(**kwargs)",
                    )
                )

    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        scoped = list(_scope_nodes(function))
        assignments = []
        saves = []
        for node in scoped:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                targets = []
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in ("id", "pk")
                    and isinstance(target.value, ast.Name)
                    and _is_literal(node.value)
                ):
                    assignments.append((target.value.id, target.attr, node.lineno))
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "save"
                and isinstance(node.func.value, ast.Name)
            ):
                saves.append((node.func.value.id, node.lineno))
        for name, attribute, line in assignments:
            if any(saved_name == name and saved_line > line for saved_name, saved_line in saves):
                findings.append(
                    SequenceFinding(
                        path, line, "P5c", "a", f"{name}.{attribute}=<literal> before save()"
                    )
                )

    for rule, finding_class, pattern, text in _ADVISORY_RULES:
        for match in pattern.finditer(source):
            findings.append(
                SequenceFinding(
                    path, _line_number(source, match.start()), rule, finding_class, text
                )
            )

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {target.id for target in targets if isinstance(target, ast.Name)}
            if "databases" in names and node.value is not None:
                segment = ast.get_source_segment(source, node.value) or ""
                if "s014_secondary" in segment:
                    findings.append(
                        SequenceFinding(
                            path,
                            node.lineno,
                            "D2-SECONDARY",
                            "secondary-database",
                            "databases declaration includes s014_secondary",
                        )
                    )
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "filter", "exclude")
                and any(
                    keyword.arg in ("id", "pk") and _is_literal(keyword.value)
                    for keyword in node.keywords
                )
            ):
                findings.append(
                    SequenceFinding(
                        path,
                        node.lineno,
                        "D2-LITERAL-READ",
                        "b/c-review",
                        "literal id lookup; classify table as seeded or emptied",
                    )
                )
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "order_by"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ("id", "pk", "-id", "-pk")
            ):
                findings.append(
                    SequenceFinding(
                        path, node.lineno, "D2-ORDER-ID", "d", "ordering by id"
                    )
                )

    return findings


def _scan_sql_or_powershell(path, source):
    findings = []
    lines = source.splitlines()
    for rule, pattern in _RAW_RULES.items():
        for match in pattern.finditer(source):
            line = _line_number(source, match.start())
            text = lines[line - 1].lstrip() if line <= len(lines) else ""
            if text.startswith("#") or text.startswith("--"):
                continue
            findings.append(
                SequenceFinding(path, line, rule, "counter-operation", match.group(0))
            )
    for match in _INSERT_WITH_ID.finditer(source):
        if not any(column.strip().lower() == "id" for column in match.group(2).split(",")):
            continue
        line = _line_number(source, match.start())
        text = lines[line - 1].lstrip() if line <= len(lines) else ""
        if text.startswith("#") or text.startswith("--"):
            continue
        findings.append(
            SequenceFinding(
                path,
                line,
                "P4",
                "a",
                f"INSERT INTO {match.group(1)} (id)",
            )
        )
    return findings


def _scan_file(path, root):
    relative = path.relative_to(root).as_posix()
    source = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".py":
        findings = _scan_python(relative, source)
    else:
        findings = _scan_sql_or_powershell(relative, source)
    return findings


def scan_inventory(inventory_path, root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    inventory_path = Path(inventory_path)
    findings = []
    for item in inventory_path.read_text(encoding="utf-8-sig").splitlines():
        relative = item.strip().replace("\\", "/")
        if not relative:
            continue
        path = root.joinpath(*relative.split("/"))
        if not path.is_file():
            raise FileNotFoundError(f"inventory path is missing: {relative}")
        findings.extend(_scan_file(path, root))
    return sorted(findings, key=lambda finding: (finding.path, finding.line, finding.rule))


def scan_repository(root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    findings = []
    for directory in _SCAN_ROOTS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in (".py", ".ps1", ".sql"):
                findings.extend(_scan_file(path, root))
    return sorted(findings, key=lambda finding: (finding.path, finding.line, finding.rule))


def _allow_reason(finding):
    for path, rule, reason in _ALLOWLIST:
        if finding.path == path and (
            finding.rule == rule or (rule == "P5" and finding.rule.startswith("P5"))
        ):
            return reason
    return None


def guard_result(findings):
    stale = []
    for path, rule, _reason in _ALLOWLIST:
        if not any(
            finding.path == path
            and (finding.rule == rule or (rule == "P5" and finding.rule.startswith("P5")))
            for finding in findings
        ):
            stale.append((path, rule))
    refused = [
        finding
        for finding in findings
        if finding.rule.startswith("P")
        and finding.finding_class != "a-prime"
        and _allow_reason(finding) is None
    ]
    return refused, stale


def _fixture_guard_patterns():
    SQL_SETVAL = "SELECT setval('sample_id_seq', 1, false)"
    SQL_RESTART = "TRUNCATE sample_table RESTART IDENTITY"
    SQL_ALTER = "ALTER SEQUENCE sample_id_seq RESTART WITH 1"
    SQL_INSERT = "INSERT INTO sample_table (id, value) VALUES (1, 2)"
    Model = apps.get_model("core", "GovernedRule")
    if False:
        Model.objects.create(id=1)
        Model.objects.bulk_create([Model(pk=2)])
        row = Model(id=3)
        row.pk = 4
        row.save()
        Model.objects.get_or_create(defaults={"id": 5})
    return SQL_SETVAL, SQL_RESTART, SQL_ALTER, SQL_INSERT


class S015SequenceDisciplineGuardTests(SimpleTestCase):
    def test_scanner_refuses_each_pattern_and_accepts_prose(self):
        samples = {
            "P1": "statement = \"SELECT setval('sample_id_seq', 1, false)\"",
            "P2": 'statement = "TRUNCATE sample_table RESTART IDENTITY"',
            "P3": 'statement = "ALTER SEQUENCE sample_id_seq RESTART WITH 1"',
            "P4": 'statement = "INSERT INTO sample_table (id, value) VALUES (1, 2)"',
            "P5a": "from core.models import GovernedRule\nGovernedRule.objects.create(id=1)",
            "P5b": (
                "from core.models import GovernedRule\n"
                "GovernedRule.objects.bulk_create([GovernedRule(pk=2)])"
            ),
            "P5c": "def write(row):\n    row.pk = 3\n    row.save()",
            "P5d": (
                "from core.models import GovernedRule\n"
                'GovernedRule.objects.get_or_create(defaults={"id": 4})'
            ),
        }
        for expected, source in samples.items():
            with self.subTest(rule=expected):
                self.assertIn(
                    expected,
                    {finding.rule for finding in _scan_python("fixture.py", source)},
                )
        prose = '"""setval( and RESTART IDENTITY are mentioned as prose."""'
        self.assertEqual(_scan_python("fixture.py", prose), [])

    def test_in_memory_model_construction_is_not_a_database_write(self):
        source = (
            "from core.models import Identity\n"
            "def target_shape():\n"
            "    applicant = Identity(pk=7)\n"
            "    return applicant\n"
        )
        self.assertFalse(
            any(finding.finding_class == "a" for finding in _scan_python("fixture.py", source))
        )

    def test_mocked_manager_and_method_are_not_database_writes(self):
        source = (
            "from core.models import Course\n"
            "from unittest.mock import patch\n"
            "def manager_case():\n"
            "    course = Course(pk=41)\n"
            "    with patch.object(Course, 'objects', manager):\n"
            "        course.save()\n"
            "def method_case():\n"
            "    course = Course(pk=42)\n"
            "    with patch.object(Course, 'save'):\n"
            "        course.save()\n"
        )
        self.assertFalse(
            any(finding.finding_class == "a" for finding in _scan_python("fixture.py", source))
        )

    def test_tuple_bulk_create_persists_explicit_model_id(self):
        source = (
            "from core.models import LibraryResourceVersion\n"
            "def write():\n"
            "    version = LibraryResourceVersion(pk=7)\n"
            "    LibraryResourceVersion.objects.bulk_create((version,))\n"
        )
        self.assertIn(
            "P5b",
            {finding.rule for finding in _scan_python("fixture.py", source)},
        )

    def test_conditional_writes_are_listed_but_not_refused(self):
        source = (
            "from core.models import GovernedRule\n"
            "GovernedRule.objects.get_or_create(pk=1)\n"
            "GovernedRule.objects.create(id=generated_id)\n"
            "GovernedRule(**values)\n"
            "UnknownModel(pk=2)\n"
        )
        findings = _scan_python("fixture.py", source)
        for line_number, sample_name, expected_listed in (
            (2, "GovernedRule.objects.get_or_create(pk=1)", True),
            (3, "GovernedRule.objects.create(id=generated_id)", True),
            (4, "GovernedRule(**values)", True),
            (5, "UnknownModel(pk=2)", False),
        ):
            self.assertEqual(
                any(
                    finding.line == line_number
                    and finding.finding_class == "a-prime"
                    for finding in findings
                ),
                expected_listed,
                sample_name,
            )
        refused, _stale = guard_result(findings)
        self.assertEqual(refused, [])

    def test_repository_scan_has_no_unallowed_writes_and_no_stale_allowances(self):
        refused, stale = guard_result(scan_repository())
        self.assertEqual(refused, [])
        self.assertEqual(stale, [])

    def test_backend_overrides_both_resets(self):
        from intevia.test_postgresql_backend.operations import DatabaseOperations

        self.assertIn("sequence_reset_by_name_sql", DatabaseOperations.__dict__)
        self.assertIn("sequence_reset_sql", DatabaseOperations.__dict__)


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--guard", action="store_true")
    parser.add_argument("--inventory")
    args = parser.parse_args(argv)
    if args.list:
        if not args.inventory:
            parser.error("--list requires --inventory")
        findings = scan_inventory(args.inventory)
        for finding in findings:
            reason = _allow_reason(finding)
            suffix = f" | allowed: {reason}" if reason else ""
            print(finding.render() + suffix)
        return

    findings = scan_repository()
    for finding in findings:
        reason = _allow_reason(finding)
        suffix = f" | allowed: {reason}" if reason else ""
        print(finding.render() + suffix)
    refused, stale = guard_result(findings)
    for path, rule in stale:
        print(f"{path} | {rule} | stale-allowance | no matching finding")
    if refused or stale:
        raise RuntimeError("sequence discipline guard refused the repository")
    print("SEQUENCE_DISCIPLINE_GUARD=PASS")


if __name__ == "__main__":
    main()
