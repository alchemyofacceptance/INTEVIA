"""CLK-05, the code half: Python reads the time only through the INTEVIA Clock.

The catalogue half (``tests/test_intevia_clock_only_one.py``) holds what the database stores. This module holds what
is written in Python, to the acceptance rule agreed before the build (the CK-2j1 code-half rule v0.2, accepted by the
Human Governor on 15 Nov 2026 as a bounded claim; v0.3 corrects only how the files are read; v0.4 carries his
disposition of review pass 1, rulings 1 A to 4 A of 15 Nov 2026). The claim is bounded, and its bound is part of it: in the
inventory named below, at the tested commit, the check reports each written occurrence of a form listed below, and a
finding is cleared only by an exact entry on the allow-list (``ALLOWED``) that the Human Governor approved, with its
count. Providers that are not listed, anything decided at run time and data converted by the server are outside
this evidence (the rule's section 5), and so is any file outside the inventory.

The forms (rule section 2). Files are read from the tested tree's files on disk (rule v0.3: the route's snapshot holds
the commit's tracked files and no ``.git``), each identified by the git blob id of its bytes, decoded as Python decodes them, and parsed
with ``ast``. A name is resolved at its occurrence from the binding visible there: import aliases, relative and local
imports, a direct local alias, a named project re-export followed through ``from`` imports within the inventory; a
parameter or a local assignment of something else shadows the import.

- P1, a host clock: a reference to one of ``HOST_REFERENCES``; ``time.clock_gettime`` or ``clock_gettime_ns`` called
  with ``time.CLOCK_REALTIME``; a converter of ``CONVERTERS`` written as a bare reference, or called with its time
  operand omitted, or given as the literal ``None`` (each of the six reads ``None`` as now).
- P2, Django's database clock: a reference to ``Now`` or ``TransactionNow``; ``Func`` or a subclass (a Django class is
  known by the object it is, ``django_object``, under its public and its defining spelling alike) given
  ``function=`` as a string naming a time function of the catalogue half's F1 or F2; ``auto_now`` or
  ``auto_now_add`` with a true literal on a resolved Django field class other than
  ``core.clock_fields.ClockDateTimeField`` itself.
- P3, SQL written in Python: each ``str`` constant that is not a docstring, each ``bytes`` constant that decodes as
  ASCII, and each f-string (its replacement fields read as Python and standing in the text as parameters; each
  literal part, and a format specification's literal text, is also read on its own, rule v0.4). Quoting and the
  folding of unquoted names are PostgreSQL's. Where
  PostgreSQL's parser reads the text as statements, or as an expression after ``SELECT``, it is held to the catalogue
  half's F1 to F6; otherwise to F3, the landed census patterns.
- P4: ``getattr(<resolved module or class>, "<constant>")`` is the reference it names.
- P5: a file the check must read and cannot (a decoding failure, a syntax error, a frozen file that is changed or
  missing) fails the check. It is never read as empty.

The inventory read by this part (rule section 3, groups 1 to 3): the product (``PRODUCT``, migrations directories
left out); the frozen migrations, by path and git blob (``FROZEN_MIGRATIONS``: a file whose blob matches is not read,
a changed or missing one fails, a migration not listed is read as product code); and the frozen interim tests, by
path and git blob (``FROZEN_INTERIM_TESTS``: the test files that held a host read on the baseline, tree ecd77698;
one whose blob no longer matches is read).

A name is resolved from the bindings of the whole scope (rule v0.4, section 2): a function's body is read after the
scope it is written in, so an import or a definition written below it binds there; in the scope being read a
parameter or a local assignment of something else still shadows. A chained assignment is an alias for each target;
annotations, defaults and decorators are read where they stand; a reference inside a longer expression is reported.

The tests that move the clock (rule section 3, groups 3 to 5). A file under ``tests/`` that is not a matching interim
file is selected where it holds a move trigger, or reaches one through its static call graph; a matching interim
file is selected where it reaches one (rule v0.4, group 4 (b)). Every other test file (``is_test_file``) that is not a
matching interim file is held to P1, P2 and P4 in its own text, and not to P3 (ruling 2 A). A move trigger is a
reference to ``intevia.test_postgresql_backend.clock.advance`` or ``advance_to`` (direct, aliased or by P4), or a
string whose SQL calls ``intevia_test_clock_advance`` (the name as text is not a call). A selected file is held to P1
to P5 in its own text and in each definition it reaches. The graph's edges are calls resolved statically to a
function, method or class in the product or under ``tests/``: same-file calls, nested functions, recursion (each
definition visited once), calls on ``self`` and ``cls`` within a class hierarchy the inventory holds, a method found
in Python's method resolution order (C3, ``method_order``), a class's
``__init__`` where the class is called, and the unittest lifecycle entries (``LIFECYCLE``) of each class a selected
file's classes inherit from. A reached definition contributes its body, its default-argument expressions, its
decorators, and a reached class its class body; the other definitions of its file contribute nothing. An edge into
a file outside those two groups ends there. An allow-list entry clears an occurrence at its source, on each chain;
a finding in a reached definition is reported against the selected file with the chain that reaches it.

It reads no clock itself, takes nothing from the database and never moves the clock.
"""

import ast
import hashlib
import importlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from unittest import mock

import pgparse
from django.test import SimpleTestCase

from tests import test_intevia_clock_only_one as catalogue
from verification.clock import clock_census_v1_2 as census

# P1. References reported wherever they are written (rule section 2).
HOST_REFERENCES = frozenset({
    "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.datetime.today", "datetime.date.today",
    "time.time", "time.time_ns", "django.utils.timezone.now",
})
# P1. Converters: the keyword of the time operand (None where it is positional only; it is the first positional
# argument in each), and whether the literal None is read as now by that converter (CPython 3.12.13 and Django
# 5.2.18, the VC's run of the Advisor's premises). formatdate reads None as now too: the rule's list of five left it
# out by oversight (the VC's reading, with the go for CK-2j2a), so it is reported as gmtime(None) is.
CONVERTERS = {
    "time.gmtime": (None, True), "time.localtime": (None, True), "time.ctime": (None, True),
    "django.utils.timezone.localtime": ("value", True), "django.utils.timezone.localdate": ("value", True),
    "email.utils.formatdate": ("timeval", True),
}
REALTIME_CALLS = frozenset({"time.clock_gettime", "time.clock_gettime_ns"})
REALTIME_CLOCK = "time.CLOCK_REALTIME"
# P2.
DATABASE_CLOCKS = frozenset({"django.db.models.functions.Now", "django.contrib.postgres.functions.TransactionNow"})
CLOCK_FIELD = "core.clock_fields.ClockDateTimeField"
FUNC = "django.db.models.Func"
TIME_FUNCTIONS = frozenset(name.lower() for name in catalogue.CALLED_BY_NAME) | frozenset(
    reading.lower() for reading in catalogue.KEYWORDS.values())
# Public and defining-module spellings of one object.
SAME_OBJECT = (
    ("django.db.models.functions.datetime.", "django.db.models.functions."),
    ("django.db.models.expressions.Func", FUNC),
    ("django.db.models.fields.", "django.db.models."),
)
# What a wildcard import binds: only a name whose module and export are known from the lists above.
LISTED = HOST_REFERENCES | frozenset(CONVERTERS) | REALTIME_CALLS | DATABASE_CLOCKS | {REALTIME_CLOCK, FUNC}
# The inventory's first group (rule section 3, 1): the product.
PRODUCT = ("core/", "src/", "email_foundation/", "intevia/", "intevia_test_clock/", "manage.py", "run.py")
# Groups 4 and 5: what moves the clock, and the lifecycle entries unittest calls without a written call.
MOVE_TRIGGERS = frozenset({"intevia.test_postgresql_backend.clock.advance", "intevia.test_postgresql_backend.clock.advance_to"})
MOVE_PROCEDURE = "intevia_test_clock_advance"
LIFECYCLE = ("setUp", "tearDown", "setUpClass", "tearDownClass", "setUpTestData")
UNKNOWN = object()   # a name bound to something the reading does not follow: it shadows whatever was imported


class Unreadable(Exception):
    """A file the check must read and cannot (P5): its path, and why in the reader's words."""

    def __init__(self, path, why):
        super().__init__("%s cannot be read as Python: %s" % (path, why))
        self.path, self.why = path, why


def blob_id(data):
    """The git blob id of a file's bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def module_name(path):
    parts = list(PurePosixPath(path).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def time_function(written):
    """Say whether ``function=`` names a time function of F1 or F2: the name unqualified, or qualified with
    ``pg_catalog``, folded to lower case as PostgreSQL folds an unquoted name. Any other schema's function of that
    name is another function."""
    if not isinstance(written, str):
        return False
    name = written.strip().lower()
    if name.startswith("pg_catalog."):
        name = name[len("pg_catalog."):]
    return name in TIME_FUNCTIONS


_DJANGO_OBJECTS = {}


def django_object(dotted):
    """The object a dotted Django name is, found as Python finds it (the longest importable module, then its
    attributes), or None. This is how one class is known under each of its spellings: ``django.db.models.Count`` and
    ``django.db.models.aggregates.Count`` are the same object. Only names under ``django.`` are looked up; importing a
    Django module reads no clock and touches no database."""
    if dotted not in _DJANGO_OBJECTS:
        found, parts = None, dotted.split(".")
        if parts[0] == "django":
            for cut in range(len(parts), 0, -1):
                try:
                    found = importlib.import_module(".".join(parts[:cut]))
                except ImportError:
                    continue
                try:
                    for name in parts[cut:]:
                        found = getattr(found, name)
                except AttributeError:
                    found = None
                break
        _DJANGO_OBJECTS[dotted] = found
    return _DJANGO_OBJECTS[dotted]


def django_class(dotted, base):
    """Say whether a dotted name is the Django class ``base`` or a Django subclass of it, by the objects themselves.
    A name that only looks like one (it ends in ``Field``, or sits in a module of functions) is not one."""
    found, above = django_object(dotted), django_object(base)
    return isinstance(found, type) and isinstance(above, type) and issubclass(found, above)


class Inventory:
    """Files by path, as bytes, with what the reading learns of them: each module's tree, the names its top level
    binds, and its classes' bases."""

    def __init__(self, files):
        self.files = {path: (data if isinstance(data, bytes) else data.encode("utf-8")) for path, data in files.items()}
        self.trees, self.exports, self.bases, self.reading, self.read = {}, {}, {}, set(), {}

    def blob(self, path):
        return blob_id(self.files[path]) if path in self.files else None

    def tree(self, path):
        if path not in self.trees:
            try:
                self.trees[path] = ast.parse(self.files[path], filename=path)   # bytes: the BOM and coding cookie apply
            except (SyntaxError, ValueError, LookupError) as error:
                raise Unreadable(path, str(error).split("\n")[0]) from error
        return self.trees[path]

    def path_of(self, module):
        """The inventory's file for a dotted module name, or None."""
        stem = module.replace(".", "/")
        for path in (stem + ".py", stem + "/__init__.py", "src/" + stem + ".py", "src/" + stem + "/__init__.py"):
            if path in self.files:
                return path
        return None

    def index(self, path):
        """One file's whole reading, kept: its findings, the calls it writes, its move triggers and its definitions.
        Raises Unreadable (P5)."""
        if path not in self.read:
            reader = Reader(self, path)
            reader.read()
            self.read[path] = reader
        return self.read[path]

    def top_level(self, path):
        """The names a module's top level binds (a re-export is followed through these)."""
        if path not in self.exports:
            if path in self.reading:
                return {}
            self.reading.add(path)
            try:
                reader = Reader(self, path, quiet=True)
                reader.read()
                self.exports[path] = reader.scopes[0][1]
            finally:
                self.reading.discard(path)
        return self.exports[path]


def _true_literal(node):
    """Say whether an option's written value is a literal that Python holds true (True, 1, anything non-empty)."""
    if isinstance(node, ast.Constant):
        return bool(node.value) and node.value is not Ellipsis
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return bool(node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.keys)
    return False


def _constant_getattr(node):
    """The (object, name) of ``getattr(object, "name")`` written with a string constant, else None (P4)."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and not node.keywords
            and len(node.args) == 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
        return node.args[0], node.args[1].value
    return None


def _escape_string_opens(text, i):
    """Say whether the quote at ``i`` opens a PostgreSQL escape string: it follows ``E`` or ``e`` written as a prefix of
    its own, not as the last letter of a longer word."""
    if i == 0 or text[i - 1] not in "eE":
        return False
    return i == 1 or not (text[i - 2].isalnum() or text[i - 2] in "_$" or text[i - 2] > "\x7f")


def _parameters(text):
    """SQL text with each driver parameter (``%s``, ``%(name)s``) that stands in code replaced by ``$1``. Inside a
    quoted string, a quoted name, a dollar-quoted string or a comment it stays text. Quoting is PostgreSQL's: a quote
    is doubled to write it, and in an escape string (``E'...'``) a backslash also takes the next character, so
    ``\\'`` does not end the string. A text whose quoting does not close is returned as it is."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"":
            escapes = ch == "'" and _escape_string_opens(text, i)
            end = i + 1
            while end < n:
                if escapes and text[end] == "\\":
                    end += 2
                elif text[end] != ch:
                    end += 1
                elif text.startswith(ch * 2, end):
                    end += 2
                else:
                    break
            if end >= n:
                return text
            out.append(text[i:end + 1])
            i = end + 1
        elif text.startswith("--", i):
            end = catalogue._comment_end(text, i)
            end = n if end < 0 else end
            out.append(text[i:end])
            i = end
        elif text.startswith("/*", i):
            depth, end = 1, i + 2
            while depth and end < n:
                if text.startswith("/*", end):
                    depth, end = depth + 1, end + 2
                elif text.startswith("*/", end):
                    depth, end = depth - 1, end + 2
                else:
                    end += 1
            if depth:
                return text
            out.append(text[i:end])
            i = end
        elif ch == "$" and catalogue._DOLLAR_TAG.match(text, i):
            tag = catalogue._DOLLAR_TAG.match(text, i).group(0)
            end = text.find(tag, i + len(tag))
            if end < 0:
                return text
            out.append(text[i:end + len(tag)])
            i = end + len(tag)
        elif text.startswith("%s", i):
            out.append("$1")
            i += 2
        elif text.startswith("%(", i) and text.find(")s", i) > 0 and text[i + 2:text.find(")s", i)].isidentifier():
            out.append("$1")
            i = text.find(")s", i) + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def sql_readings(text):
    """The time reads written in one Python string, read as SQL (P3): as statements where PostgreSQL's parser reads
    it so, else as an expression after ``SELECT``, each held to F1 to F6 as a routine body is; otherwise the landed
    census patterns alone (F3)."""
    prepared = _parameters(text)
    for form, prefix in (("sql", ""), ("expressions", "SELECT ")):
        try:
            statements = pgparse.parse(prefix + prepared)
        except pgparse.PGQueryError:
            continue
        if statements:
            found = [reading for reading in catalogue.time_readings(form, prepared) if not reading.startswith("unreadable")]
            return sorted(found)
    found = set()
    for pattern, _meaning in census.DB_CLOCKS:
        for match in pattern.finditer(text):
            found.add("in a string: " + "".join(match.group(0).split()).lower())
    return sorted(found)


def sql_calls(text, name):
    """Say whether a Python string, read as SQL (statements, else an expression), calls the routine ``name``: a CALL,
    or a call in an expression. The name written as text, in a string or a comment, is not a call."""
    prepared, found = _parameters(text), []

    def walk(node):
        if isinstance(node, dict):
            for call in (node.get("FuncCall"), node.get("funccall")):   # an expression's call, and a CALL statement's
                if not isinstance(call, dict):
                    continue
                names = [part.get("String", {}).get("sval") for part in call.get("funcname", [])]
                found.append(bool(names) and names[-1] == name)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    if name not in text.lower():   # PostgreSQL folds an unquoted name to lower case; the parser's tree holds it folded
        return False
    for prefix in ("", "SELECT "):
        try:
            walk(pgparse.parse(prefix + prepared))
        except pgparse.PGQueryError:
            continue
        break
    return any(found)


class Reader(ast.NodeVisitor):
    """One file's reading. ``findings`` holds (path, enclosure, form, label, line, column): the enclosure is ``module``,
    ``Class.method`` or a nested function's path (a default-argument expression and a decorator belong to the
    definition they are written on; a lambda belongs to the definition it is written in); the form is what an
    allow-list entry names (the canonical name of a P1 or P2 form, or a P3 string's exact text); the label says the
    form's kind and what was read. ``edges`` holds each call written, by enclosure: ("name", what it resolves to) or
    ("self", the class, the method). ``triggers`` holds each move trigger's (enclosure, line). ``definitions`` maps
    each function's and class's path within the file to its kind."""

    def __init__(self, inventory, path, quiet=False):
        self.inventory, self.path, self.quiet = inventory, path, quiet
        self.module = module_name(path)
        # Each scope: its kind, what each name is bound to now, and the last thing each name was known to be.
        self.scopes = [("module", {}, {})]
        # Function bodies wait until the scope they are written in has been read whole (rule v0.4, section 2): a body
        # sees its module's bindings as the whole file makes them. ``complete`` counts the enclosing scopes of the
        # body being read that are already whole.
        self.deferred, self.complete = [], 0
        self.enclosure, self.findings, self.docstrings = [], [], set()
        self.edges, self.triggers, self.definitions, self.receivers = [], [], {}, [None]

    def read(self):
        tree = self.inventory.tree(self.path)
        self._docstring(tree)
        for statement in tree.body:
            self.visit(statement)
        while self.deferred:
            self.deferred.pop(0)()
        self.findings.sort(key=lambda finding: finding[4:6])   # in the order they are written
        return self.findings

    # -- names

    def _bind(self, name, value):
        _kind, names, known = self.scopes[-1]
        names[name] = value
        if value is not UNKNOWN:
            known[name] = value

    def _lookup(self, name):
        """What a name is bound to where it is written. In the scope being read, that is the binding made so far (a
        parameter or a local assignment of something else shadows). In an enclosing scope already read whole, it is
        the binding the whole scope makes; where that scope also bound the name to something the reading does not
        know, the last known binding stands, so the reading reports more and never less."""
        inner = True
        for depth in range(len(self.scopes) - 1, -1, -1):
            kind, names, known = self.scopes[depth]
            if kind == "class" and not inner:
                continue   # a class body's names are not visible inside its methods
            inner = inner and kind == "class"
            if name in names:
                value = names[name]
                if value is UNKNOWN and depth < self.complete:
                    value = known.get(name, UNKNOWN)
                return None if value is UNKNOWN else value
            inner = False
        return None

    def canonical(self, dotted, depth=0):
        """One object's name: a project re-export followed to what it names, and the public spelling of an object
        Django defines in another module."""
        parts = dotted.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            path = self.inventory.path_of(".".join(parts[:cut]))
            if path is None or path == self.path or depth > 20:
                continue
            bound = self.inventory.top_level(path).get(parts[cut])
            if isinstance(bound, str) and bound != ".".join(parts[:cut + 1]):
                return self.canonical(".".join([bound] + parts[cut + 1:]), depth + 1)
            break
        for written, public in SAME_OBJECT:
            if dotted.startswith(written):
                return public + dotted[len(written):]
        return dotted

    def resolve(self, node):
        """What an expression names, as a dotted name, or None where the reading does not know (P4 included)."""
        if isinstance(node, ast.Name):
            return self._lookup(node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            return self.canonical(base + "." + node.attr) if base else None
        named = _constant_getattr(node)
        if named:
            base = self.resolve(named[0])
            return self.canonical(base + "." + named[1]) if base else None
        return None

    def _bases(self, target):
        if target not in self.inventory.bases and "." in target:
            path = self.inventory.path_of(target.rsplit(".", 1)[0])
            if path and path != self.path:
                self.inventory.top_level(path)
        return self.inventory.bases.get(target, ())

    def _is_field(self, target, seen=()):
        """Say whether a resolved name is a Django model field class, or a project class with one among its bases."""
        if target is None or target in seen:
            return False
        return django_class(target, "django.db.models.Field") or any(
            self._is_field(base, (*seen, target)) for base in self._bases(target))

    def _is_func(self, target, seen=()):
        """Say whether a resolved name is Django's ``Func``, a Django subclass of it, or a project class with one among
        its bases."""
        if target is None or target in seen:
            return False
        return django_class(target, FUNC) or any(self._is_func(base, (*seen, target)) for base in self._bases(target))

    # -- findings

    def _report(self, node, kind, form, read=None):
        if not self.quiet:
            label = "%s %s" % (kind, read if read is not None else form)
            self.findings.append((self.path, self._where(), form, label, node.lineno, node.col_offset))

    def _where(self):
        return ".".join(self.enclosure) or "module"

    def _docstring(self, node):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            self.docstrings.add(id(body[0].value))

    def _sql(self, node, text, shown=()):
        """Read one text as SQL and report what it holds, but for the readings in ``shown`` (an f-string's parts have
        reported those). Returns what was read."""
        if self.quiet:
            return []
        found = sql_readings(text)
        fresh = [reading for reading in found if reading not in shown]
        if fresh:
            self._report(node, "P3", text, "; ".join(fresh))
        if sql_calls(text, MOVE_PROCEDURE):
            self.triggers.append((self._where(), node.lineno))
        return found

    def _reference(self, node, target):
        """Report a bare reference (one that is not being called) to a listed form. True where it was one."""
        if target in MOVE_TRIGGERS:
            self.triggers.append((self._where(), node.lineno))
        elif target in HOST_REFERENCES or target in CONVERTERS:
            self._report(node, "P1", target)
        elif target in DATABASE_CLOCKS:
            self._report(node, "P2", target)
        else:
            return False
        return True

    # -- statements that bind names

    def visit_Import(self, node):
        for alias in node.names:
            self._bind(alias.asname or alias.name.split(".")[0], alias.name if alias.asname else alias.name.split(".")[0])

    def visit_ImportFrom(self, node):
        module = node.module or ""
        if node.level:
            package = list(PurePosixPath(self.path).parent.parts)
            package = package[:len(package) - (node.level - 1)]
            module = ".".join(package + ([module] if module else []))
        for alias in node.names:
            if alias.name == "*":
                for listed in LISTED:
                    if listed.startswith(module + "."):
                        export = listed[len(module) + 1:].split(".")[0]
                        self._bind(export, module + "." + export)
            else:
                self._bind(alias.asname or alias.name, self.canonical(module + "." + alias.name))

    def _targets(self, target, value=None):
        if isinstance(target, ast.Name):
            named = self.resolve(value) if value is not None else None
            self._bind(target.id, named if named else UNKNOWN)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._targets(element)
        elif isinstance(target, ast.Starred):
            self._targets(target.value)
        else:
            self.visit(target)

    def visit_Assign(self, node):
        self.visit(node.value)
        for target in node.targets:
            self._targets(target, node.value)   # A = B = x: a direct alias for every target

    def visit_AnnAssign(self, node):
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self._targets(node.target, node.value)

    def visit_AugAssign(self, node):
        self.visit(node.value)
        self._targets(node.target)

    def visit_NamedExpr(self, node):
        self.visit(node.value)
        self._targets(node.target, node.value)

    def visit_For(self, node):
        self.visit(node.iter)
        self._targets(node.target)
        for statement in node.body + node.orelse:
            self.visit(statement)

    visit_AsyncFor = visit_For

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._targets(item.optional_vars)
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node):
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._bind(node.name, UNKNOWN)
        for statement in node.body:
            self.visit(statement)

    def _comprehension(self, node):
        self.scopes.append(("function", {}, {}))
        for generator in node.generators:
            self.visit(generator.iter)
            self._targets(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for part in ("elt", "key", "value"):
            if hasattr(node, part):
                self.visit(getattr(node, part))
        self.scopes.pop()

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _comprehension

    # -- definitions

    def _function(self, node, name):
        arguments = node.args
        in_class = self.scopes[-1][0] == "class"
        if name is not None:
            self.enclosure.append(name)   # its defaults, annotations and decorators are read where they stand, and belong to it
            self.definitions[self._where()] = "function"
        every = [argument for argument in arguments.posonlyargs + arguments.args + arguments.kwonlyargs
                 + [arguments.vararg, arguments.kwarg] if argument is not None]
        for default in arguments.defaults + [default for default in arguments.kw_defaults if default is not None]:
            self.visit(default)
        for argument in every:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if getattr(node, "returns", None) is not None:
            self.visit(node.returns)
        if name is not None:
            for decorator in node.decorator_list:
                self.visit(decorator)
            self._bind(name, "%s.%s" % (self.module, self._where()))
        if self.quiet:
            if name is not None:
                self.enclosure.pop()
            return   # a module's top level is all a re-export needs
        first = (arguments.posonlyargs + arguments.args)[:1]
        receiver = ((first[0].arg, ".".join(self.enclosure[:-1])) if in_class and first and name is not None
                    else self.receivers[-1])
        state = (list(self.scopes), list(self.enclosure), receiver)
        if name is not None:
            self.enclosure.pop()
        self.deferred.append(lambda: self._body(node, name, every, state))

    def _body(self, node, name, every, state):
        """Read a function's body, once the scope it is written in has been read whole."""
        scopes, enclosure, receiver = state
        kept = (self.scopes, self.enclosure, self.receivers, self.complete)
        self.scopes, self.enclosure, self.receivers, self.complete = scopes + [("function", {}, {})], enclosure, [None, receiver], len(scopes)
        for argument in every:
            self._bind(argument.arg, UNKNOWN)
        if name is None:
            self.visit(node.body)
        else:
            self._docstring(node)
            for statement in node.body:
                self.visit(statement)
        self.scopes, self.enclosure, self.receivers, self.complete = kept

    def visit_FunctionDef(self, node):
        self._function(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self._function(node, None)

    def visit_ClassDef(self, node):
        top = len(self.scopes) == 1
        name = "%s.%s" % (self.module, node.name)
        self.enclosure.append(node.name)
        self.definitions[self._where()] = "class"
        for part in node.decorator_list + node.bases + [keyword.value for keyword in node.keywords]:
            self.visit(part)
        if top:
            self.inventory.bases[name] = tuple(base for base in map(self.resolve, node.bases) if base)
        self._bind(node.name, name if top else UNKNOWN)
        self.scopes.append(("class", {}, {}))
        self._docstring(node)
        func_class = top and self._is_func(name)
        for statement in node.body:
            if (func_class and isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id == "function"
                    and isinstance(statement.value, ast.Constant) and time_function(statement.value.value)):
                self._report(statement, "P2", "function=%s on %s" % (statement.value.value, name))
            if not (self.quiet and isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))):
                self.visit(statement)
        self.enclosure.pop()
        self.scopes.pop()

    # -- expressions

    def visit_Call(self, node):
        if _constant_getattr(node):
            target = self.resolve(node)
            if target and self._reference(node, target):
                return
            self.generic_visit(node)
            return
        target = self.resolve(node.func)
        receiver = self.receivers[-1]
        if target in MOVE_TRIGGERS:
            self.triggers.append((self._where(), node.lineno))
        elif target is not None:
            self.edges.append((self._where(), "name", target))
        elif (receiver and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
              and node.func.value.id == receiver[0] and self._lookup(receiver[0]) is None):
            self.edges.append((self._where(), "self", "%s.%s" % (self.module, receiver[1]), node.func.attr))
        unpacked = any(isinstance(argument, ast.Starred) for argument in node.args) or any(
            keyword.arg is None for keyword in node.keywords)
        if target in HOST_REFERENCES:
            self._report(node, "P1", target)
        elif target in CONVERTERS:
            keyword, none_is_now = CONVERTERS[target]
            operand = node.args[0] if node.args else next(
                (given.value for given in node.keywords if keyword and given.arg == keyword), None)
            literal_none = isinstance(operand, ast.Constant) and operand.value is None
            if (operand is None and not unpacked) or (literal_none and none_is_now):
                self._report(node, "P1", target)
        elif target in REALTIME_CALLS:
            if node.args and self.resolve(node.args[0]) == REALTIME_CLOCK:
                self._report(node, "P1", target)
        elif target in DATABASE_CLOCKS:
            self._report(node, "P2", target)
        elif target is not None and target != CLOCK_FIELD and self._is_field(target):
            options = [(given.arg, given.value) for given in node.keywords if given.arg is not None]
            for given in node.keywords:
                if given.arg is None and isinstance(given.value, ast.Dict):
                    options += [(key.value, value) for key, value in zip(given.value.keys, given.value.values, strict=True)
                                if isinstance(key, ast.Constant) and isinstance(key.value, str)]
            for option, value in options:
                if option in ("auto_now", "auto_now_add") and _true_literal(value):
                    self._report(node, "P2", "%s on %s" % (option, target))
        elif target is not None and self._is_func(target):
            for given in node.keywords:
                if given.arg == "function" and isinstance(given.value, ast.Constant) and time_function(given.value.value):
                    self._report(node, "P2", "function=%s on %s" % (given.value.value, target))
        if target is None:
            self.visit(node.func)
        elif isinstance(node.func, ast.Attribute):
            self.visit(node.func.value)   # time.time.__call__(): the reference inside stays a reference
        for argument in node.args:
            self.visit(argument)
        for given in node.keywords:
            self.visit(given.value)

    def visit_Attribute(self, node):
        target = self.resolve(node)
        if not (target and self._reference(node, target)):
            self.visit(node.value)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            target = self.resolve(node)
            if target:
                self._reference(node, target)

    def visit_Constant(self, node):
        if id(node) in self.docstrings:
            return
        if isinstance(node.value, str):
            self._sql(node, node.value)
        elif isinstance(node.value, bytes):
            try:
                self._sql(node, node.value.decode("ascii"))
            except UnicodeDecodeError:
                pass   # bytes that are not ASCII are not SQL text

    def visit_JoinedStr(self, node):
        """An f-string (rule v0.4, section 2, P3). Its replacement fields are read as Python. Each literal part, and
        the literal text of a format specification, is read as SQL on its own. The whole, with a parameter standing
        for each field, is read as well, and reports what no part showed; a whole that does not parse clears nothing
        a part shows."""
        literal = [part for part in node.values if isinstance(part, ast.Constant)]
        if len(literal) == len(node.values):
            self._sql(node, "".join(part.value for part in literal))   # no field: one text
            return
        text, shown = [], set()
        for part in node.values:
            if isinstance(part, ast.Constant):
                text.append(part.value)
                shown.update(self._sql(part, part.value))
            else:
                text.append("%s")   # a replacement field stands in the text as a parameter, and is read as Python
                self.visit(part.value)
                if part.format_spec is not None:
                    for piece in part.format_spec.values:
                        if isinstance(piece, ast.Constant):
                            shown.update(self._sql(piece, piece.value))
                        else:
                            self.visit(piece.value)
        self._sql(node, "".join(text), shown)


def read_file(inventory, path):
    """One file's findings, in the order they are written. Raises Unreadable (P5)."""
    return inventory.index(path).findings


def group_of(path, inventory, frozen_migrations, frozen_interim):
    """The inventory group a path is read under (rule section 3, groups 1 to 3), or None where this part does not
    read it: ``product``; ``frozen migration`` (its blob matches: not read); ``changed migration`` (listed, and its
    blob differs: it fails, and is read); ``unlisted migration`` (read as product code); ``interim test`` (its blob
    matches: not read); ``changed interim test`` (read)."""
    if not path.endswith(".py"):
        return None
    if path in PRODUCT or any(path.startswith(prefix) for prefix in PRODUCT if prefix.endswith("/")):
        if "migrations" not in PurePosixPath(path).parts[:-1]:
            return "product"
        if path not in frozen_migrations:
            return "unlisted migration"
        return "frozen migration" if inventory.blob(path) == frozen_migrations[path] else "changed migration"
    if path.startswith("tests/") and path in frozen_interim:
        return "interim test" if inventory.blob(path) == frozen_interim[path] else "changed interim test"
    return None


def is_test_file(path):
    """Say whether a file under tests/ is a test file: one unittest's discovery loads (``test*.py``). A support file
    beside it (``tests/support.py``) is read where a selected file reaches it (rule section 7, Q20: the unused
    definition beside a reached one is clear), and is not held in its own text."""
    return path.startswith("tests/") and path.endswith(".py") and PurePosixPath(path).name.startswith("test")


def in_graph(path):
    """Say whether a file's definitions can be reached (rule section 3, 5): the product without its migrations, and
    any file under tests/."""
    return path.startswith("tests/") or group_of(path, None, {}, {}) == "product"


def _class_at(inventory, dotted):
    """Where a class is defined, as (path, path within the file), where the product or tests/ holds it; else None."""
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        path = inventory.path_of(".".join(parts[:cut]))
        if path is None:
            continue
        within = ".".join(parts[cut:])
        return (path, within) if in_graph(path) and inventory.index(path).definitions.get(within) == "class" else None
    return None


def method_order(inventory, dotted):
    """A class's method resolution order as Python computes it (C3), over the bases the reading resolved. A base the
    product and tests/ do not hold stands in the order and has no bases of its own here. Where the written bases have
    no consistent order (Python would refuse the class), the bases are taken as written."""
    def bases(name):
        at = _class_at(inventory, name)
        return list(inventory.bases.get("%s.%s" % (module_name(at[0]), at[1]), ())) if at else []

    def linear(name, seen):
        if name in seen:
            return [name]
        above = bases(name)
        sequences = [linear(base, (*seen, name)) for base in above] + [list(above)]
        order = [name]
        while any(sequences):
            sequences = [sequence for sequence in sequences if sequence]
            head = next((sequence[0] for sequence in sequences
                         if not any(sequence[0] in other[1:] for other in sequences)), sequences[0][0])
            order.append(head)
            sequences = [[each for each in sequence if each != head] for sequence in sequences]
        return order

    return linear(dotted, ())


def definition_of(inventory, dotted, seen=()):
    """The definitions a resolved name reaches, as (path, path within the file): the function, the method (found on
    the class or, failing that, on the first class of its method resolution order that defines it), or the class
    with its ``__init__``. Empty where the name is not a definition in the product or under tests/: the edge ends
    there."""
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        path = inventory.path_of(".".join(parts[:cut]))
        if path is None:
            continue
        if not in_graph(path):
            return []
        module, within = ".".join(parts[:cut]), ".".join(parts[cut:])
        definitions = inventory.index(path).definitions
        if definitions.get(within) == "function":
            return [(path, within)]
        if definitions.get(within) == "class":
            return [(path, within), *definition_of(inventory, dotted + ".__init__", seen)]
        owner, _dot, method = within.rpartition(".")
        if definitions.get(owner) == "class":
            for each in method_order(inventory, "%s.%s" % (module, owner))[1:]:
                at = _class_at(inventory, each)
                if at and inventory.index(at[0]).definitions.get("%s.%s" % (at[1], method)) == "function":
                    return [(at[0], "%s.%s" % (at[1], method))]
        return []
    return []


def _lifecycle(inventory, path):
    """The lifecycle entries unittest calls on the classes a file defines, where a class they inherit from in the
    product or under tests/ defines them."""
    found = []
    reader = inventory.index(path)
    for within, kind in reader.definitions.items():
        if kind == "class" and "." not in within:
            for entry in LIFECYCLE:
                found.extend(definition_of(inventory, "%s.%s.%s" % (reader.module, within, entry)))
    return found


def reached(inventory, path):
    """Each definition a file reaches through the static call graph, with the chain that reaches it: the file's own
    calls and its classes' lifecycle entries first, then each reached definition's calls, each visited once."""
    chains, queue = {}, []

    def follow(edges, chain):
        for edge in edges:
            name = edge[2] if edge[1] == "name" else "%s.%s" % (edge[2], edge[3])
            for unit in definition_of(inventory, name):
                if unit not in chains:
                    chains[unit] = (*chain, "%s::%s" % unit)
                    queue.append(unit)

    reader = inventory.index(path)
    follow(reader.edges, (path,))
    for unit in _lifecycle(inventory, path):
        if unit not in chains:
            chains[unit] = (path, "%s::%s" % unit)
            queue.append(unit)
    while queue:
        unit = queue.pop(0)
        follow([edge for edge in inventory.index(unit[0]).edges if edge[0] == unit[1]], chains[unit])
    return chains


def judge(inventory, allowed=(), frozen_migrations=(), frozen_interim=()):
    """Read an inventory as the rule says. Returns the selected test files, and what is held against the inventory as
    records: ("P5", path, what is wrong: a frozen migration ``missing`` or ``changed``, or ``unreadable, needed`` and
    what for; the reader's own words are kept in ``inventory.unreadable``); ("allow-list", sentence); ("source", path, line, enclosure, label) for a finding in a
    file read whole; ("chain", selected file, chain, path, line, enclosure, label) for a finding in a definition a
    selected file reaches."""
    frozen_migrations, frozen_interim = dict(frozen_migrations), dict(frozen_interim)
    records, whole, selected, chains, own = [], [], [], [], []
    for path, blob in sorted(frozen_migrations.items()):
        if inventory.blob(path) != blob:
            records.append(("P5", path, "missing" if path not in inventory.files else "changed"))
    inventory.unreadable = {}

    def readable(path, purpose):
        try:
            return inventory.index(path)
        except Unreadable as unreadable:
            inventory.unreadable[unreadable.path] = unreadable.why
            records.append(("P5", unreadable.path, "unreadable, needed " + purpose))
            return None

    for path in sorted(inventory.files):
        group = group_of(path, inventory, frozen_migrations, frozen_interim)
        read_whole = group in ("product", "unlisted migration", "changed migration", "changed interim test")
        if read_whole and readable(path, "as %s" % ("a " + group if group != "product" else "product code")):
            whole.append(path)
    # Groups 3 to 5 (rule v0.4). A file under tests/ is selected where it holds a move trigger or reaches one; it is
    # then read whole, with each definition it reaches. A matching interim file is selected where it reaches one (D1
    # A: before any test in it moves the clock). Every other test file that is not a matching interim file is held to
    # P1, P2 and P4 in its own text (ruling 2 A of 15 Nov 2026); P3 is held only in a file read whole.
    for path in sorted(inventory.files):
        if not (path.startswith("tests/") and path.endswith(".py")):
            continue
        interim = group_of(path, inventory, frozen_migrations, frozen_interim) == "interim test"
        reader = readable(path, "to say whether this test file moves the clock")
        if reader is None:
            continue
        try:
            reach = reached(inventory, path)
        except Unreadable as unreadable:
            inventory.unreadable[unreadable.path] = unreadable.why
            records.append(("P5", unreadable.path, "unreadable, needed to follow a call from " + path))
            continue
        reaches = any(trigger[0] == unit[1] for unit in reach for trigger in inventory.index(unit[0]).triggers)
        moves = reaches if interim else (bool(reader.triggers) or reaches)
        if moves:
            selected.append(path)
            if path not in whole:
                whole.append(path)
            chains.append((path, reach))
        elif not interim and path not in whole and is_test_file(path):
            own.append(path)

    def held(path):
        return [finding for finding in inventory.index(path).findings if path in whole or not finding[3].startswith("P3 ")]

    occurrences = {}
    for path in whole + own:
        for finding in held(path):
            occurrences[(finding[0], finding[4], finding[5], finding[2])] = finding
    by_chain = []
    for path, reach in chains:
        for unit, chain in sorted(reach.items()):
            for finding in inventory.index(unit[0]).findings:
                if finding[1] == unit[1]:
                    occurrences[(finding[0], finding[4], finding[5], finding[2])] = finding
                    by_chain.append((path, chain, finding))
    counted = {}
    for finding in occurrences.values():
        counted.setdefault(finding[:3], []).append(finding)
    cleared = set()
    for entry in allowed:
        path, enclosure, form, count, reason = entry
        observed = len(counted.get((path, enclosure, form), ()))
        if not (isinstance(reason, str) and reason.strip()):
            records.append(("allow-list", "allow-list: the entry for %s in %s (%s) gives no reason." % (form, enclosure, path)))
        if observed != count:
            records.append(("allow-list", "allow-list: the entry for %r in %s (%s) allows %d and %d are written. An entry "
                            "clears exactly the number the Human Governor approved." % (form, enclosure, path, count, observed)))
        else:
            cleared.add((path, enclosure, form))
    for path in sorted(whole + own):
        for finding in held(path):
            if finding[:3] not in cleared:
                records.append(("source", finding[0], finding[4], finding[1], finding[3]))
    for path, chain, finding in by_chain:
        if finding[:3] not in cleared:
            records.append(("chain", path, chain, finding[0], finding[4], finding[1], finding[3]))
    return sorted(selected), records


def code_problems(inventory, allowed=(), frozen_migrations=(), frozen_interim=()):
    """What the code half holds against an inventory, as sentences; empty where it holds nothing. ``allowed``: entries
    of (file, enclosure, form, count, reason)."""
    advice = ("Python reads the time only through the INTEVIA Clock (src/intevia/services/clock.py); use the Clock here, or "
              "ask the Human Governor for an allow-list entry.")
    problems = []
    for record in judge(inventory, allowed, frozen_migrations, frozen_interim)[1]:
        if record[0] == "allow-list":
            problems.append(record[1])
        elif record[0] == "P5" and record[2] in ("missing", "changed"):
            problems.append("P5 %s: this frozen migration is %s. A migration is never changed once it has landed; a new one is "
                            "added." % record[1:])
        elif record[0] == "P5":
            problems.append("P5 %s cannot be read as Python: %s (%s). A file that cannot be read fails the check; it is never "
                            "read as empty." % (record[1], inventory.unreadable.get(record[1], "no reason was kept"), record[2]))
        elif record[0] == "source":
            problems.append("%s:%d, in %s: %s. %s" % (*record[1:], advice))
        else:
            _kind, selected, chain, path, line, enclosure, label = record
            problems.append("%s moves the clock and reaches %s:%d, in %s: %s. The chain: %s. A test that moves the clock, and "
                            "each definition it reaches, reads the time only through the INTEVIA Clock. %s"
                            % (selected, path, line, enclosure, label, " -> ".join(chain), advice))
    return problems


def tested_tree(root):
    """The inventory of the tested tree (rule v0.3, section 3): each ``*.py`` file under ``root``, read from disk as
    bytes and keyed by its path from ``root``. Nothing is asked of git: the route runs the tests from a snapshot of
    the tested commit that holds its tracked files and no ``.git``. A directory named ``__pycache__`` is never
    entered. A root that is no directory, or a file that cannot be read, is unreadable (P5), never empty."""
    root = Path(root)
    if not root.is_dir():
        raise Unreadable(str(root), "the tested tree is not a directory")
    files = {}

    def refuse(error):
        raise Unreadable(str(error.filename or root), "the directory could not be listed: %s" % error.strerror)

    for folder, folders, names in os.walk(root, onerror=refuse):
        folders[:] = sorted(name for name in folders if name != "__pycache__")
        for name in sorted(names):
            if name.endswith(".py"):
                full = Path(folder) / name
                path = full.relative_to(root).as_posix()
                try:
                    files[path] = full.read_bytes()
                except OSError as error:
                    raise Unreadable(path, "the file could not be read: %s" % (error.strerror or error)) from error
    return Inventory(files)


# The frozen migrations (rule section 3, group 2): each migration on main 8a4efb8c, with the code landing's core 0104
# and email_foundation 0002, by path and git blob. A file whose blob matches is not read: what it installs is the
# catalogue half's, and its historical Python is a stated baseline. A migration is never changed once it has landed.
FROZEN_MIGRATIONS = (
    ("core/migrations/0001_initial.py", "6155d9eeb7f5ff7027861b3e69a93916e102658e"),
    ("core/migrations/0002_role_alter_profile_display_name_profilerole.py", "e651c03f76517b1803c527600eb6fcd2e411b1a2"),
    ("core/migrations/0003_seed_triad_roles.py", "71783b06f68561ad7195449fe91538a5bf5edef2"),
    ("core/migrations/0004_merge_20260609_0555.py", "533cd5fb0c042f7ea8c15cc0edd2e471c90d1cab"),
    ("core/migrations/0005_slice001_contribution_lifecycle.py", "58f7983420721f250e847d786255e684bb01fe92"),
    ("core/migrations/0006_event_lifecycle.py", "5c5465046b6537d5d665f0fc0dc58a7df3307013"),
    ("core/migrations/0007_library_foundation.py", "211406e0ecacd6ff8517a3b6623d0b7aca680dcc"),
    ("core/migrations/0008_service_foundation.py", "391f030a221b1d614196d5f93327f4f0fa1213d0"),
    ("core/migrations/0009_care_response_foundation.py", "f23a11b5fd42cd6664b32af4e6c6ae2596cd85ab"),
    ("core/migrations/0010_event_registration_foundation.py", "2573def23d44c1664242b7459181f1f91249fb7e"),
    ("core/migrations/0011_s007_identity_rename.py", "75c664c8026ab1181746e4ba218579f8b27aef7a"),
    ("core/migrations/0012_s007_identity_foundation.py", "b4749bc8610cddeddbaef3b93db70daf7051566e"),
    ("core/migrations/0013_s007_identity_constraints.py", "197373925b0eadf58f38bfb2d5a56c2de93134c6"),
    ("core/migrations/0014_s008_event_attendance_foundation.py", "5987306ddb67120bc063e7c7a1e11aa739d8d27d"),
    ("core/migrations/0015_s011b_event_resource_relationship.py", "b50820c71c555cc825011b183d5675665e589cbe"),
    ("core/migrations/0016_s012_service_activity_orchestration.py", "78690a9faf7218a4a80976a93fcab5373bcf7a1d"),
    ("core/migrations/0017_s013_profile_effect.py", "d22d880f18eaee480a243562daf0c7179948b318"),
    ("core/migrations/0018_s014_education_course_foundation.py", "51b494ca33fa55bd6dd66f274ddf0a17bc08d63a"),
    ("core/migrations/0019_s015_living_organism_foundation.py", "bcd2abb45e73a1ea9ba3762095f1549e3cc4abaa"),
    ("core/migrations/0020_s015_genesis_identity_completeness.py", "f3d803ea04efbcfd81ba261f3cc6971db472e3c0"),
    ("core/migrations/0021_s015_recorded_chain_and_bitemporal_fold.py", "8512f7ff7890a0ac8256e3d612d8c2e48b1e4ae3"),
    ("core/migrations/0022_s015_governed_chain_contract.py", "0e36be22e2dc48ced3543b5a63956091c4e4b149"),
    ("core/migrations/0023_s015_canonical_form_s015f4.py", "d7abac1b803efbcd2022639e600e3dbb4e3de09e"),
    ("core/migrations/0024_s015_compact_commitment_preimages.py", "401ea788aae8715a1a3ef59b883a02c18489e1ff"),
    ("core/migrations/0025_s015_application_routes.py", "24ce096102a4f6c002afb2097ec0c4b56ce366b9"),
    ("core/migrations/0026_s015_application_admission_time.py", "b3df9f2b8615f5d34f45281f9dd1778689549ee0"),
    ("core/migrations/0027_s015_application_authority_lineage.py", "1762575040d08535831e3944e11dd91744fdfffe"),
    ("core/migrations/0028_s015_application_lineage_chain.py", "4f3e062c4b3e18389e54c016d0f92854a200497f"),
    ("core/migrations/0029_s015_application_gateway.py", "4de95bb911dbb347eea38a9ad2676d100dd3262c"),
    ("core/migrations/0030_s015_automatic_application.py", "6ebcf234b907b1fe190b20b5626188b93e417cab"),
    ("core/migrations/0031_s1fu_admission_actor_requirement.py", "d036fa16f213f7acdccf0a18721bfb32892566cc"),
    ("core/migrations/0032_s015_register_cleanup_nonblocking.py", "db76f13cf7b86d133f472d5f140d43793d2ef49e"),
    ("core/migrations/0033_s015_application_authority.py", "62953de784d395a61c18c3f00f64fdb39e011a36"),
    ("core/migrations/0034_s015_authority_decisions.py", "8687be9842ac697214a46000e2f762c0306528e3"),
    ("core/migrations/0035_s015_role_chain_actor_capacity.py", "29b042b7fac71169d1a79643c2403677434d6bd6"),
    ("core/migrations/0036_s2b_role_attribution_guards.py", "1ea5967a3609ec4012b7199ec6e2e820a6a7fb91"),
    ("core/migrations/0037_s2b_office_appointment_consent.py", "5b377b5ba7191c8ee0780879cb35902df751b679"),
    ("core/migrations/0038_s2b_coordinator_nomination_policy.py", "e18cb9cc8d84e9f41581e757d28256e48d52fcc4"),
    ("core/migrations/0039_s2b_role_suspension_settings.py", "d026afb4fbd1a220f8578a95591e6f8259e84f17"),
    ("core/migrations/0040_s2b_role_actor_nullable_state.py", "5121edc36acbd77a328abf5b0830bfbd438d3f1a"),
    ("core/migrations/0041_r4s2_assign_current_membership.py", "02759e00fb025506db6be99b52047228e619fc5d"),
    ("core/migrations/0042_sb1_founding_terms_phases.py", "6dbd82edf6c77cae5f9196ccc51b7a60712e7dac"),
    ("core/migrations/0043_p1_governed_registration.py", "ba086c8bdf7fb861d3d03e326b128aa670d64d74"),
    ("core/migrations/0044_sb2_birthing_snapshot.py", "d503cca2d696d7e44d6d4ca404a53635e97a6bd9"),
    ("core/migrations/0045_sbg_governance_formation.py", "b709df927ccba9817cd643bf1706f10412eeb1d4"),
    ("core/migrations/0046_se_entitlement_foundation.py", "2ef139a27c662aced3fd92a3c616b3ba051ab382"),
    ("core/migrations/0047_sek1_platform_rule_statement_start.py", "e66d3b4ee4b3150e6d987fd929b394aecb07b44d"),
    ("core/migrations/0048_se_entitlement_granted_event_guard.py", "f72404883a000f71029bb58a31825203ab412214"),
    ("core/migrations/0049_h100_command_intake.py", "40e5dd6230918efbfaedc5dedf6cdde733c2f52a"),
    ("core/migrations/0050_clk_mono_1_cache_coordinate_detail.py", "8bdf7d16cfd730f221ff40c3aa9f30ebd1025ae6"),
    ("core/migrations/0051_sr_role_definitions.py", "34eac4777661ca05788639434b051ccb3a5b3016"),
    ("core/migrations/0052_sr_office_guard_widening.py", "44da3740f2f5e658ae7e7fbdff456ffdaef51f56"),
    ("core/migrations/0053_sr_office_safeguards.py", "e632f79d7fe1dffdd7babf92b0e25b007c3445cc"),
    ("core/migrations/0054_c1b_relationships.py", "f2753d7d0a555e3d4af099bcefb163461bacc616"),
    ("core/migrations/0055_c2_coverage.py", "f3519d02af0400d776a1102fb55e0e40abe1cfb8"),
    ("core/migrations/0056_c3_authority_lifecycle.py", "375d4a51b681407cba6e7c265e4c7e0eeb8e36e9"),
    ("core/migrations/0057_shared_event_append.py", "363361352a749b6afe7107b75badb8d9eb429a42"),
    ("core/migrations/0058_circle_creation_writer.py", "3c21a31c56c040c9134a22d22cf9d8314e566289"),
    ("core/migrations/0059_operating_circles.py", "71545523cc40ca174e1c6afa0edca68e56c842de"),
    ("core/migrations/0060_circle_coordinators.py", "b1fbff0427996a15e5f3482e2bc279be686fca8c"),
    ("core/migrations/0061_circle_qualified_activation.py", "bdd1f8aefef6c4a967e141a4554340e075657e75"),
    ("core/migrations/0062_root_genesis.py", "dc06f3294d391ab73e7769afc90a47d126012833"),
    ("core/migrations/0063_root_circles.py", "be63ea8eef275fbcd213181f47758792ceb9ab59"),
    ("core/migrations/0064_root_publication_declaration.py", "c7b16f3a7d25b702190fa1243dca0e2b1912c534"),
    ("core/migrations/0065_root_continuity.py", "67283b8ae247d132280eca11565fa057ece1419c"),
    ("core/migrations/0066_law_records.py", "cd4447a513eb5f46b4df282c6caee8e63568e2e1"),
    ("core/migrations/0067_law_acts.py", "b4db1cbb87c0bcadf2041349bf7473ee3e8146d5"),
    ("core/migrations/0068_library_laws_read.py", "a6834e7ee5f102fb5b139c3ae8c6f78300b055a1"),
    ("core/migrations/0069_library_policy_publication.py", "2e1ff11f3d07d679cf9d21b4c403cd6b9a702550"),
    ("core/migrations/0070_law_route_parameter_qualification.py", "6238c99274f59d29133d8783748de2db511bfbc0"),
    ("core/migrations/0071_guiding_intentions.py", "ad9e0139a8f03c03cf4812ac6402d26d7aa2b068"),
    ("core/migrations/0072_discussion_boards.py", "90a2468f328b3bbd685ed381a787041fc6df529a"),
    ("core/migrations/0073_discussion_posts.py", "59b9c46ab7cbbb9bc3f0c61f5134d80e98d66791"),
    ("core/migrations/0074_cq_clarity.py", "7c8f3a4e57eba53f4ffcfc7e8bdcd35aec798d79"),
    ("core/migrations/0075_cq_decisions.py", "ad2df29a7b80589e8ad33c0c64e50d439ac1e834"),
    ("core/migrations/0076_cq_authority.py", "7bbcc476bb3481a3a82a5fae26d346f325353c8a"),
    ("core/migrations/0077_cq_settings_authority.py", "b170d5aed5ee419c0e8b9979f24a7054037f9928"),
    ("core/migrations/0078_cq_circle_reporting.py", "c321159477c5ae69254d0ad32e949553bbe6e392"),
    ("core/migrations/0079_elect_and_seat.py", "b551daa2a02b62299e5a3c64786fed0e084d3b57"),
    ("core/migrations/0080_elect_authority_and_vacancy.py", "18650c3fd564efa58a5328233d29a5b3dc8d8093"),
    ("core/migrations/0081_hard_voting_authority.py", "f63928dca6154910a41f2c47716cf139b6ed1570"),
    ("core/migrations/0082_elect_term_continuity.py", "b0a47e8f7ea2b6894e422a30525f7fb063be9946"),
    ("core/migrations/0083_elect_board_continuity.py", "5137e79006e308ef7979f78ee16793d9f4def518"),
    ("core/migrations/0084_committee_mandates.py", "b1adefb0d58389e46c01c565c4b696f13cc95303"),
    ("core/migrations/0085_board_voting.py", "71337cce1c9a6d882394e3f8774d806a241c3227"),
    ("core/migrations/0086_board_continuity.py", "dee2ead401aa970442e7ec85b559a8499fe94355"),
    ("core/migrations/0087_members_stand_down.py", "784bc627751bd1afe63fd31ee05b4c145886c101"),
    ("core/migrations/0088_constitutional_board_makeup.py", "b680a33d8df3f1b01e243f39635364cf14a220c0"),
    ("core/migrations/0089_members_restore_board.py", "b1f8c304f391b2b811424cc8692d5f470691985c"),
    ("core/migrations/0090_stand_down_supersession.py", "d7a2aeb0b5c0d2aecf21ce4c05736ad272be0d24"),
    ("core/migrations/0091_te_engine_storage.py", "65bc3e09622e52c4dcc79d2bdd13b76ad6481146"),
    ("core/migrations/0092_te_elect_joins.py", "fde4ea0bcc83f7a4cbc1ba58a9aca028caaffa36"),
    ("core/migrations/0093_te_cq_joins.py", "ff409dfd230e964bfe66024fe481652cfa748c33"),
    ("core/migrations/0094_te_committee.py", "e92375a34c37fd138bd9e3764398a8cc685c6eb5"),
    ("core/migrations/0095_te_c2_join.py", "841b552e5b545abebf0233b3c911591612c9010c"),
    ("core/migrations/0096_intevia_clock.py", "2f5438a050b477a43bd9d39ceaecbdc472c63c93"),
    ("core/migrations/0097_clock_storage_0021_0049.py", "2d0b915b7c0a154ce2d0066776747f643dbfef13"),
    ("core/migrations/0098_clock_storage_0050_0058.py", "0a0abb895ae77460b8667e42b6a7d1b41c893cfc"),
    ("core/migrations/0099_clock_storage_0059_0072.py", "e63fd10c19ab81ea2e9e1b0840a820d9e0212e35"),
    ("core/migrations/0100_clock_storage_0073_0080.py", "280b9c2353563c960d69c6b73688de9f2e835f95"),
    ("core/migrations/0101_clock_storage_0081_0087.py", "c5d308918f50cbdc4d9e0d6a896aa1b630f6ab74"),
    ("core/migrations/0102_clock_storage_0088_0095.py", "8fea5062137a01b54528a1741f9af88a277d0b5c"),
    ("core/migrations/0103_clock_storage_column_defaults.py", "b7d7b2f5901b1cd5aa48f7614d1712ec44a0e474"),
    ("core/migrations/0104_clock_automatic_time_fields.py", "dc6ecd1b82aab545503328edd07bda8b30476fb4"),
    ("core/migrations/__init__.py", "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"),
    ("core/migrations/_board_restoration.py", "f6e2a093db67440ad2ba4ec148536936829204f2"),
    ("core/migrations/_board_stand_down.py", "920295f3d1eb8fca6720e56371b36bf6df22f679"),
    ("core/migrations/_board_stand_down_effect.py", "facb6acf67b32d59634e55478ba3841fb93d480c"),
    ("core/migrations/_clock_conversion.py", "bca9cab9aaf6464ae9bf0786df8843b431f8544c"),
    ("core/migrations/_committee_board_work.py", "0b317d3657aa0c8c7edf22714c94c67e7cc58746"),
    ("core/migrations/_committee_mandates.py", "de2e337637d808b22eb1bf5dbafbc2864fac70d0"),
    ("core/migrations/_cq3_gi.py", "01de41a135b33ba4fe19386ac61365650b66a3ae"),
    ("core/migrations/_cq3_referrals.py", "bc175382621a3f0197f12e14d3c3b8cbeaaa8eec"),
    ("core/migrations/_elect_ballot.py", "5bbb1a4468ad7b2d4e55190a78010c56d12de09e"),
    ("core/migrations/_elect_continuity.py", "6a2a7e6e6c61ab6ccbf8de3da398be309547df3a"),
    ("core/migrations/_elect_effects.py", "12b6fdb617aae758e97d61f9623dd641381e154d"),
    ("core/migrations/_elect_terms.py", "66d9f35a3a72ae340cfbe6565dad21bcf49de12a"),
    ("core/migrations/_elect_transfer.py", "1e061ce2683b33f317ccedbdf7272cac082528f4"),
    ("core/migrations/_elect_vacancy.py", "84ff6d99d2310a1e4a33f52c5cdef4294390c4b4"),
    ("email_foundation/migrations/0001_initial.py", "b50ade78af6f3f41136ab1afaab8e1bb737331ca"),
    ("email_foundation/migrations/0002_clock_automatic_time_fields.py", "21503bb3b0e644d3ee797bf6bb26282fb22b3ec6"),
    ("email_foundation/migrations/__init__.py", "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"),
    ("intevia_test_clock/migrations/0001_moving_part.py", "5f2c04f090693c04ed42db72af13c2bb5cff6a65"),
    ("intevia_test_clock/migrations/__init__.py", "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"),
)
# The frozen interim tests (rule section 3, group 3; the Human Governor's ruling D1 A): each test file under tests/
# that held a host read (a P1 form) on the baseline, tree ecd77698, by path and that tree's git blob. While a file's
# blob matches, its own reads stay interim. Once the file changes it is read as any selected file is.
FROZEN_INTERIM_TESTS = (
    ("tests/test_em_a.py", "51dd5191221cb594a4c38dfac6c5d885c39d0c16"),
    ("tests/test_sr_h_safeguards.py", "36fa5df44bfaa90a39ebf8e0a32a371af312fad1"),
    ("tests/test_timed_events.py", "d3f2f1b0a7f7f418865782513d170bfa58cc63cb"),
)

# The allow-list (rule section 6): the only way a finding is cleared. Each entry: the file, the qualified enclosure,
# the exact form (a P1 or P2 form's name, or a P3 string's exact text), the number of occurrences it clears, and the
# reason. An entry is added only with the Human Governor's approval. None is approved yet.
ALLOWED = ()

# The agreed cases (rule v0.2 section 7; the Advisor's pre-build agreement). Each: its name, its files, and what the
# rule's verdict is in words. A file is its lines, each with the findings the rule gives that line (none: clear).
_NOW, _TODAY = "P3 now()", "P3 clock word in a string: 'tomorrow'"
CASES = (
    ("Q1", {"core/q1.py": [
        ("from django.utils import timezone as z", []),
        ("z.localtime(value=aware, timezone=zone)", []),
        ("z.localdate(aware, timezone=zone)", []),
        ("z.localdate(timezone=zone)", ["P1 django.utils.timezone.localdate"]),
        ("z.localtime(value=None, timezone=zone)", ["P1 django.utils.timezone.localtime"]),
    ]}, "a time supplied to Django's converters is clear; a time zone alone, or None, is not a time"),
    ("Q2", {"core/q2.py": [
        ("import time", []),
        ("time.gmtime(None)", ["P1 time.gmtime"]),
        ("time.localtime(None)", ["P1 time.localtime"]),
        ("time.ctime(None)", ["P1 time.ctime"]),
        ("time.asctime(None)", []),
        ("time.strftime('%Y', None)", []),
    ]}, "None is now for gmtime, localtime and ctime; asctime and strftime raise, and are clear"),
    ("Q3", {"core/q3.py": [
        ("from time import gmtime", []),
        ("dispatch = gmtime", ["P1 time.gmtime"]),
        ("dispatch()", ["P1 time.gmtime"]),   # the alias, called with no time: a second written occurrence
        ("gmtime(0)", []),
    ]}, "a converter used as a value is reported at the reference; a supplied time is clear"),
    ("Q4", {"core/q4.py": [
        ("import time as t", []),
        ("t.clock_gettime(t.CLOCK_REALTIME)", ["P1 time.clock_gettime"]),
        ("t.clock_gettime_ns(t.CLOCK_REALTIME)", ["P1 time.clock_gettime_ns"]),
        ("t.clock_gettime(t.CLOCK_MONOTONIC)", []),
    ]}, "the realtime clock is reported; the monotonic clock is clear (D6)"),
    ("Q5", {"core/q5.py": [
        ("from email.utils import formatdate", []),
        ("formatdate()", ["P1 email.utils.formatdate"]),
        ("formatdate(timeval=0)", []),
        ("formatdate(None)", ["P1 email.utils.formatdate"]),
        ("formatdate(timeval=None)", ["P1 email.utils.formatdate"]),
    ]}, "formatdate() and formatdate(None) are reported, as gmtime(None) is; formatdate(timeval=0) is clear"),
    ("Q6", {"core/q6.py": [
        ("from django.db.models.functions.datetime import Now", []),
        ("from django.contrib.postgres.functions import TransactionNow", []),
        ("from django.db.models import Func", []),
        ("Now()", ["P2 django.db.models.functions.Now"]),
        ("TransactionNow()", ["P2 django.contrib.postgres.functions.TransactionNow"]),
        ("Func(function='NOW')", ["P2 function=NOW on django.db.models.Func"]),
    ]}, "all three reported"),
    ("Q7", {"core/q7.py": [
        ("import time", []),
        ("def display(time):", []),
        ("    return time.time()", []),
        ("def measure():", []),
        ("    import time as t", []),
        ("    return getattr(t, 'time_ns')()", ["P1 time.time_ns"]),
    ]}, "a parameter shadows the import (clear); a local import read by constant name is reported"),
    ("Q8", {"core/providers.py": [
        ("import datetime as dt", []),
        ("D = dt.datetime", []),
    ], "core/jobs.py": [
        ("from .providers import D", []),
        ("D.now()", ["P1 datetime.datetime.now"]),
    ]}, "a named project re-export is followed: reported"),
    ("Q9", {"core/q9.py": [
        ("from datetime import *", []),
        ("stamp = datetime.now()", ["P1 datetime.datetime.now"]),
    ]}, "a wildcard import binds a known export: reported"),
    ("Q10", {"core/q10.py": [
        ("from django.db.models import DateTimeField as DT", []),
        ("from core.clock_fields import ClockDateTimeField as TheClass", []),
        ("DT(auto_now=1)", ["P2 auto_now on django.db.models.DateTimeField"]),
        ("DT(**{'auto_now_add': True})", ["P2 auto_now_add on django.db.models.DateTimeField"]),
        ("DT(auto_now=False)", []),
        ("def option(**kw): return kw", []),
        ("option(auto_now=True)", []),
        ("TheClass(auto_now_add=True)", []),
        ("ClockDateTimeField = DT", []),
        ("ClockDateTimeField(auto_now=True)", ["P2 auto_now on django.db.models.DateTimeField"]),
    ]}, "a true option on a Django field is reported, the clock's own field class is not, and a local name like it is not the class"),
    ("Q11", {"core/q11.py": [
        ("cursor.execute(\"SELECT 'tomorrow'::date\")", [_TODAY]),
        ("RawSQL(\"'tomorrow'::date\", ())", [_TODAY]),
        ("RawSQL('age(created_at)', ())", ["P3 age() with one argument"]),
    ]}, "all three reported: a fragment is read as the expression it is"),
    # Q12: both are clear, and that is the rule's stated data limit (section 5): the server reads the value
    # 'tomorrow' as a date at the cast, and neither Python string holds that as a written SQL occurrence.
    ("Q12", {"core/q12.py": [
        ("cursor.execute('SELECT %s::date', ('tomorrow',))", []),
        ("cursor.execute('SELECT %s::text', ('tomorrow',))", []),
    ]}, "both clear, by the stated limit on values the server interprets"),
    ("Q13", {"core/q13.py": [
        ("q = \"SELECT %(p)s, 'tomorrow'\"", [_TODAY]),
        ("q = \"SELECT 'now=%s'\"", ["P3 clock word in a string: 'now=%s'"]),
        ("q = 'SELECT 1 /* %(p)s, now() */'", []),
    ]}, "a parameter in code is a parameter; in a quoted string it is text; in a comment nothing is read"),
    ("Q14", {"core/q14.py": [
        ("cursor.execute(b'SELECT now()')", [_NOW]),
        ("cursor.execute(f\"SELECT 'tomorrow'::date\")", [_TODAY]),
        ("cursor.execute(f\"SELECT {col}, 'tomorrow'\")", [_TODAY]),
    ]}, "bytes and f-strings are read; the assembly of an f-string is a stated limit"),
    ("Q15", {"core/q15.py": [
        ("import time", []),
        ("def explain():", []),
        ("    '''SELECT now()'''", []),
        ("    log_example = 'SELECT now()'", [_NOW]),
        ("    return log_example, f'{time.time()}'", ["P1 time.time"]),
    ]}, "a docstring is clear; a label is reported; a replacement field is read as Python"),
    ("Q16", {"core/q16.py": [
        ("label = 'elapsed since now() -- not a statement'", ["P3 in a string: now()"]),
        ("# elapsed since now()", []),
    ]}, "text that is not SQL is held to the census patterns; a Python comment is not read"),
    ("Q28", {"core/q28.py": [
        ("from datetime import datetime as D", []),
        ("import django.db.models.functions as f", []),
        ("from time import perf_counter_ns as elapsed", []),
        ("stamp = getattr(D, 'now')", ["P1 datetime.datetime.now"]),
        ("expr = getattr(f, 'Now')", ["P2 django.db.models.functions.Now"]),
        ("ticks = elapsed()", []),
    ]}, "a reference by constant name is the reference it names; a duration clock is clear"),
    # Pass 1's cases (rule v0.4, section 7; the Human Governor's ruling 1 A of 15 Nov 2026).
    ("A6", {"core/a6.py": [
        ("def f():", []),
        ("    return t.time()", ["P1 time.time"]),
        ("import time as t", []),
    ]}, "an import written below the function binds in its body, as when Python runs it"),
    ("A7", {"core/a7.py": [
        ("import datetime as dt", []),
        ("A = B = dt.datetime", []),
        ("a = A.now()", ["P1 datetime.datetime.now"]),
        ("b = B.now()", ["P1 datetime.datetime.now"]),
    ]}, "a chained assignment is a direct alias for each target"),
    ("A8", {"core/a8.py": [
        ("import time", []),
        ("def f(t: time.time()) -> time.time(): pass", ["P1 time.time", "P1 time.time"]),
    ]}, "an argument's annotation and a return annotation are read as written"),
    ("A9", {"core/a9.py": [
        ("import time", []),
        ("x = time.time.__call__()", ["P1 time.time"]),
    ]}, "a reference stays a reference inside a longer expression"),
    ("A11", {"core/a11.py": [
        ("import time", []),
        ("def f(getattr): return getattr(time, 'time')()", ["P1 time.time"]),
    ]}, "the verdict this build gives, recorded (ruling 4 A: it reports more than Python would run, and misses nothing)"),
    ("A13", {"core/a13.py": [
        ("q = r\"SELECT E'a\\'b', %s, 'tomorrow'\"", [_TODAY]),
        ("r = \"SELECT 'it''s', %s, 'tomorrow'\"", [_TODAY]),
    ]}, "quoting follows PostgreSQL: a quote escaped inside an escape string does not end it"),
    ("A14", {"core/a14.py": [
        ("q = f\"SELECT 'tomorrow' {''} FROM agenda\"", [_TODAY]),
    ]}, "an f-string's literal part is read on its own; a whole that does not parse clears nothing"),
    ("A15", {"core/a15.py": [
        ("q = f\"{thing:SELECT now()}\"", [_NOW]),
    ]}, "the literal text of a format specification is read as a literal part"),
    ("A16", {"core/a16.py": [
        ("from django.db.models import Func", []),
        ("a = Func(function='pg_catalog.now')", ["P2 function=pg_catalog.now on django.db.models.Func"]),
        ("b = Func(function='PG_CATALOG.Now')", ["P2 function=PG_CATALOG.Now on django.db.models.Func"]),
        ("c = Func(function='other.now')", []),
    ]}, "the function's name is matched unqualified or qualified with pg_catalog, case folded; another schema's is clear"),
    ("A17", {"core/a17.py": [
        ("from django.db.models import Count", []),
        ("from django.db.models.aggregates import Count as Defined", []),
        ("from django import forms", []),
        ("a = Count('id', function='NOW', template='%(function)s()')", ["P2 function=NOW on django.db.models.Count"]),
        ("b = Defined('id', function='NOW')", ["P2 function=NOW on django.db.models.aggregates.Count"]),
        ("c = forms.DateTimeField(auto_now=True)", []),
    ]}, "one Django class is known under its public and its defining spelling; a name that only ends in Field is not a model field"),
    ("whole scope: a parameter still shadows, and a later local binding does not reach back", {"core/whole_scope.py": [
        ("def shown(time):", []),
        ("    return time.time()", []),
        ("def local():", []),
        ("    stamp = time.time()", ["P1 time.time"]),
        ("    return stamp", []),
        ("def nested():", []),
        ("    def inner(): return clock()", ["P1 time.time"]),
        ("    from time import time as clock", []),
        ("    return inner", []),
        ("import time", []),
    ]}, "shadowing is kept; a closure sees its enclosing function's bindings whole"),
    # The rows of the rule's section 4 that this part reads and that no case above writes out.
    ("rows 1, 2 and 4", {"core/rows_reported.py": [
        ("import datetime as dt, time", []),
        ("from django.utils import timezone", []),
        ("from django.utils.timezone import now", []),
        ("from django.db import models", []),
        ("a = dt.datetime.now(tz)", ["P1 datetime.datetime.now"]),
        ("f = dt.datetime.now", ["P1 datetime.datetime.now"]),
        ("b = timezone.now()", ["P1 django.utils.timezone.now"]),
        ("c = models.DateTimeField(default=timezone.now)", ["P1 django.utils.timezone.now"]),
        ("d = now()", ["P1 django.utils.timezone.now"]),
        ("e = timezone.localtime()", ["P1 django.utils.timezone.localtime"]),
        ("g = time.time_ns()", ["P1 time.time_ns"]),
        ("h = time.ctime()", ["P1 time.ctime"]),
    ]}, "reported (P1)"),
    ("rows 3, 5 and 10", {"core/rows_clear.py": [
        ("import time", []),
        ("from time import perf_counter_ns as elapsed", []),
        ("a = time.gmtime(0), time.localtime(t), time.strftime(fmt, tup)", []),
        ("b = time.monotonic(), elapsed(), time.clock_gettime(time.CLOCK_MONOTONIC)", []),
        ("c = 'SELECT 1 /* %(p)s, now() */'", []),
    ]}, "not reported"),
    ("rows 8 and 9", {"core/rows_sql.py": [
        ("a = 'created_at < now()'", [_NOW]),
        ("b = \"SELECT 'now', public.intevia_clock_statement()\"", ["P3 clock word in a string: 'now'"]),
        ("c = \"SELECT 'now', public.intevia_clock_statement() WHERE id = %s\"", ["P3 clock word in a string: 'now'"]),
        ("d = 'SELECT public.intevia_clock_statement()'", []),
    ]}, "SQL written in Python is held as a routine body is; a face is not a finding"),
)
# Q25: three ways a file cannot be read. The first is the Advisor's exact bytes.
UNREADABLE = (
    ("an unknown coding declaration", b"# coding: not-a-codec\nfrom intevia.test_postgresql_backend.clock import advance\n"),
    ("a byte-order mark against its coding cookie", b"\xef\xbb\xbf# coding: latin-1\nx = 1\n"),
    ("a syntax error", b"def broken(:\n    pass\n"),
)
# Q26: one migration in each spelling of the same host read.
MIGRATIONS = {
    "core/migrations/0001_first.py": "import time\ndef forwards(*args):\n    return getattr(time, 'time')()\n",
    "core/migrations/0002_second.py": "import time\ndef forwards(*args):\n    return time.time()\n",
}
# Group 3 (and, with selection, Q23 in the next part): a test file that reads the host clock.
LEGACY = "from time import time\ndef test_legacy():\n    assert time() > 0\n"
# Q27: one string written twice in one enclosure.
DIAGNOSTIC = "def diagnostic():\n    log('SELECT now()')\n    run('SELECT now()')\n"

# The reach (rule section 7, Q17 to Q24). Each: its name; its files; the interim list as (path, what the file was on
# the baseline: None where it is unchanged); the allow-list; the files the rule selects; what it holds against the
# inventory (judge's records); and the verdict in words.
_MOVE = "from intevia.test_postgresql_backend.clock import advance\n"
_CASE = "tests/test_case.py"
_Q17_CASE = "import time\nfrom .helpers import move\ndef test_case(delta):\n    move(delta)\n    return time.time()\n"
_Q17_HELD = [("source", _CASE, 5, "test_case", "P1 time.time")]
_Q20 = {"tests/support.py": "import time\ndef chosen(value=time.time()): return value\ndef unused(): return time.time()\n",
        _CASE: _MOVE + "from .support import chosen\nmove = advance\nvalue = chosen()\n"}
_A3_OLD = "import time\nfrom .support import go\ndef test_old():\n    go()\n    return time.time()\n"
_A4_CASE = _MOVE + "from .support import a\nmove = advance\nvalue = a()\n"
_A5_CASE = "import time\nfrom .support import a\ndef test_case():\n    a()\n    return time.time()\n"
GRAPH_CASES = (
    ("Q17", {"tests/helpers.py": _MOVE + "def move(delta): advance(delta)\n", _CASE: _Q17_CASE}, (), (),
     ["tests/helpers.py", _CASE], _Q17_HELD, "a helper moves the clock for a test: the test file is selected, and its host read reported"),
    ("Q17, the SQL twin", {"tests/helpers.py": "def move(cursor, delta): cursor.execute('CALL public.intevia_test_clock_advance(%s)', (delta,))\n",
                           _CASE: _Q17_CASE.replace("move(delta)", "move(cursor, delta)")}, (), (),
     ["tests/helpers.py", _CASE], _Q17_HELD, "the same, where the helper moves the clock by SQL"),
    ("Q18", {"tests/support.py": "import time\ndef outer(n):\n    def inner(): return time.time()\n    return outer(n-1) if n else inner()\n",
             _CASE: _MOVE + "from .support import outer\nmove = advance\nvalue = outer(1)\n"}, (), (), [_CASE],
     [("chain", _CASE, (_CASE, "tests/support.py::outer", "tests/support.py::outer.inner"), "tests/support.py", 3, "outer.inner", "P1 time.time")],
     "reported through outer to inner; the recursion ends"),
    ("Q19", {"tests/support.py": "import time\nclass StampMixin:\n    def setUp(self): self.stamp = time.time()\n"
                                 "    def helper(self): return time.ctime()\n",
             _CASE: "from unittest import TestCase\nfrom .support import StampMixin\n" + _MOVE
                    + "class Case(StampMixin, TestCase):\n    def test_move(self):\n        advance(self.delta)\n        self.helper()\n"},
     (), (), [_CASE],
     [("chain", _CASE, (_CASE, "tests/support.py::StampMixin.helper"), "tests/support.py", 4, "StampMixin.helper", "P1 time.ctime"),
      ("chain", _CASE, (_CASE, "tests/support.py::StampMixin.setUp"), "tests/support.py", 3, "StampMixin.setUp", "P1 time.time")],
     "an inherited lifecycle entry is read without a written call, and a call on self within the hierarchy is followed"),
    ("Q20", _Q20, (), (), [_CASE],
     [("chain", _CASE, (_CASE, "tests/support.py::chosen"), "tests/support.py", 2, "chosen", "P1 time.time")],
     "the reached definition's default is reported; the unused definition beside it is clear"),
    ("Q20, allowed at its source", _Q20, (), [("tests/support.py", "chosen", "time.time", 1, "the case's own entry")], [_CASE], [],
     "an allow-list entry clears the occurrence at its source, on the chain too"),
    ("Q20, a decorator", {"tests/support.py": "import time\ndef mark(value):\n    def wrap(function): return function\n    return wrap\n"
                                             "@mark(time.time())\ndef chosen(): return 1\ndef unused(): return time.time()\n",
                          _CASE: _MOVE + "from .support import chosen\nmove = advance\nvalue = chosen()\n"}, (), (), [_CASE],
     [("chain", _CASE, (_CASE, "tests/support.py::chosen"), "tests/support.py", 5, "chosen", "P1 time.time")],
     "a reached definition's decorator is reported against the test that reaches it"),
    ("Q21", {"src/intevia/services/clock.py": "def database_now():\n    return run('SELECT public.intevia_clock_statement()')\n",
             _CASE: _MOVE + "from intevia.services.clock import database_now\nmove = advance\nobserved = database_now()\n"
                    "def my_database_now():\n    import time\n    return time.time()\n"}, (), (), [_CASE],
     [("source", _CASE, 7, "my_database_now", "P1 time.time")], "reaching the approved door is clear; a name like it grants nothing"),
    ("Q22", {"tests/test_a.py": "from intevia.test_postgresql_backend.clock import advance_to\nmove_later = advance_to\n",
             "tests/test_b.py": "q = 'CALL public.intevia_test_clock_advance(%s)'\n",
             "tests/test_c.py": "q = \"SELECT 'intevia_test_clock_advance'\"\n",
             "tests/test_d.py": "from intevia.test_postgresql_backend import clock as owned\nmove = owned.advance\n",
             "tests/test_e.py": "import intevia.test_postgresql_backend.clock as owned\nmove = getattr(owned, 'advance')\n"}, (), (),
     ["tests/test_a.py", "tests/test_b.py", "tests/test_d.py", "tests/test_e.py"], [],
     "a reference selects the file, used or not, as does its alias and its constant-name spelling; a CALL selects; the name as text does not"),
    ("Q23", {"tests/test_legacy.py": LEGACY, "tests/test_control.py": LEGACY}, (("tests/test_legacy.py", "as it was"), ("tests/test_control.py", None)),
     (), [], [("source", "tests/test_legacy.py", 3, "test_legacy", "P1 time.time")],
     "a changed interim file has left the list and is read; its unchanged control stays interim"),
    ("Q23, the control reached", {"tests/test_control.py": LEGACY, "tests/test_mover.py": _MOVE + "from .test_control import test_legacy\nmove = advance\ntest_legacy()\n"},
     (("tests/test_control.py", None),), (), ["tests/test_mover.py"],
     [("chain", "tests/test_mover.py", ("tests/test_mover.py", "tests/test_control.py::test_legacy"), "tests/test_control.py", 3, "test_legacy", "P1 time.time")],
     "an interim file's definition is read once a selected file reaches it"),
    # (a) of group 4, as this build reads it: a matching interim file is not selected, even where it holds a move
    # trigger; none did on the baseline, and a file that gains one changes, leaves the list and is read.
    ("Q23, an interim file with a move trigger", {"tests/test_old_mover.py": _MOVE + "import time\nmove = advance\nstamp = time.time()\n"},
     (("tests/test_old_mover.py", None),), (), [], [], "a matching interim file is not selected while its blob matches"),
    ("Q24", {"tools/sample_helpers.py": "import time\ndef sample(): return time.time()\n",
             _CASE: _MOVE + "from tools.sample_helpers import sample\nmove = advance\nvalue = sample()\n"}, (), (), [_CASE], [],
     "the stated limit: the edge into tools/ ends there"),
    # Pass 1's cases (rule v0.4, section 7; rulings 1 A and 2 A of 15 Nov 2026).
    ("A1", {"tests/test_new.py": "import time\ndef test_new(): assert time.time() > 0\n"}, (), (), [],
     [("source", "tests/test_new.py", 2, "test_new", "P1 time.time")],
     "a new test file that does not move the clock is held to P1 in its own text"),
    ("A2", {"tests/test_sql.py": "q = 'SELECT now()'\n"}, (), (), [], [],
     "the stated limit: SQL in a test file that does not move the clock is not held"),
    ("A3", {"tests/support.py": _MOVE + "def go(): advance(1)\n", "tests/test_old.py": _A3_OLD}, (("tests/test_old.py", None),), (),
     ["tests/support.py", "tests/test_old.py"], [("source", "tests/test_old.py", 5, "test_old", "P1 time.time")],
     "an unchanged interim file that reaches a move is selected and read whole"),
    ("A3, the control", {"tests/support.py": "def go(): return 1\n", "tests/test_old.py": _A3_OLD}, (("tests/test_old.py", None),), (), [], [],
     "the same interim file stays interim while what it reaches does not move the clock"),
    ("A4", {"tests/support.py": "import time\ndef a(): return b()\ndef b(): return time.time()\n", _CASE: _A4_CASE}, (), (), [_CASE],
     [("chain", _CASE, (_CASE, "tests/support.py::a", "tests/support.py::b"), "tests/support.py", 3, "b", "P1 time.time")],
     "a helper defined below its caller is followed"),
    ("A4, the other order", {"tests/support.py": "import time\ndef b(): return time.time()\ndef a(): return b()\n", _CASE: _A4_CASE}, (), (), [_CASE],
     [("chain", _CASE, (_CASE, "tests/support.py::a", "tests/support.py::b"), "tests/support.py", 2, "b", "P1 time.time")],
     "both orders give one verdict"),
    ("A5", {"tests/support.py": _MOVE + "def a(): b()\ndef b(): advance(1)\n", _CASE: _A5_CASE}, (), (),
     ["tests/support.py", _CASE], [("source", _CASE, 5, "test_case", "P1 time.time")],
     "a move made by a helper defined below its caller selects the test"),
    ("A5, the other order", {"tests/support.py": _MOVE + "def b(): advance(1)\ndef a(): b()\n", _CASE: _A5_CASE}, (), (),
     ["tests/support.py", _CASE], [("source", _CASE, 5, "test_case", "P1 time.time")], "both orders give one verdict"),
    ("A10", {"tests/root.py": "class Root:\n    def x(self): return 1\n",
             "tests/left.py": "from .root import Root\nclass Left(Root): pass\n",
             "tests/right.py": "import time\nfrom .root import Root\nclass Right(Root):\n    def x(self): return time.time()\n",
             _CASE: _MOVE + "from .left import Left\nfrom .right import Right\nclass Case(Left, Right):\n    def test_move(self):\n"
                    "        advance(1)\n        return self.x()\n"}, (), (), [_CASE],
     [("chain", _CASE, (_CASE, "tests/right.py::Right.x"), "tests/right.py", 4, "Right.x", "P1 time.time")],
     "a method is found in Python's method resolution order: Right.x, before Root.x"),
    ("A12", {"tests/test_upper.py": "import time\nq = 'CALL public.INTEVIA_TEST_CLOCK_ADVANCE(%s)'\nstamp = time.time()\n"}, (), (),
     ["tests/test_upper.py"], [("source", "tests/test_upper.py", 3, "module", "P1 time.time")],
     "an unquoted name is folded as PostgreSQL folds it: the upper-case CALL moves the clock"),
    ("A12, the quoted name", {"tests/test_quoted.py": "q = 'CALL public.\"INTEVIA_TEST_CLOCK_ADVANCE\"(%s)'\n"}, (), (), [], [],
     "a quoted upper-case name is another identifier, and is not a call of the move"),
    ("A18", {"core/a18.py": "from tools.helpers import x\ny = x()\n", "tools/helpers.py": "def broken(:\n"}, (), (), [],
     [("P5", "tools/helpers.py", "unreadable, needed as product code")],
     "the verdict this build gives, recorded (ruling 4 A): it fails closed on an unreadable file outside the inventory"),
    ("Q25, a test file", {"tests/test_unreadable.py": "def broken(:\n"}, (), (), [],
     [("P5", "tests/test_unreadable.py", "unreadable, needed to say whether this test file moves the clock")],
     "a test file that cannot be read cannot be shown not to move the clock: it fails"),
)


class OnlyOneClockCodeTests(SimpleTestCase):
    def test_the_reading_of_python_judges_the_agreed_cases(self):
        """CLK-05, code half, the reader's own table. Each of the Advisor's cases Q1 to Q16 and Q25 to Q28 (rule
        section 7), pass 1's cases A6 to A9, A11 and A13 to A17 (rule v0.4), and the rows of section 4 that this part reads, is
        read by the check's own functions from a scratch inventory and must get the verdict the rule gives it."""
        for name, files, expected in CASES:
            inventory = Inventory({path: "\n".join(line for line, _labels in lines) + "\n" for path, lines in files.items()})
            for path, lines in files.items():
                written = {number: labels for number, (_line, labels) in enumerate(lines, 1) if labels}
                read = {}
                for _path, _enclosure, _form, label, number, _column in read_file(inventory, path):
                    read.setdefault(number, []).append(label)
                self.assertEqual(read, written, "CLK-05 code half, case %s (%s): the reading of %s differs from the verdicts the "
                                                "agreed rule gives, line by line." % (name, expected, path))
        # Q25 (P5): a file that cannot be read fails the check, whatever makes it unreadable.
        for name, data in UNREADABLE:
            problems = code_problems(Inventory({"core/unreadable.py": data}))
            self.assertEqual(len(problems), 1, "CLK-05 code half, Q25 (%s): an unreadable product file must fail the check." % name)
            self.assertIn("P5 core/unreadable.py cannot be read as Python", problems[0],
                          "CLK-05 code half, Q25 (%s): the failure must name the file and say it could not be read." % name)
        # Q26: a frozen migration is not read while its blob matches, in either spelling; changed or missing, it fails;
        # a migration that is not listed is read as product code.
        frozen = Inventory(MIGRATIONS)
        listed = [(path, frozen.blob(path)) for path in sorted(MIGRATIONS)]
        self.assertEqual(code_problems(frozen, frozen_migrations=listed), [],
                         "CLK-05 code half, Q26: a frozen migration whose blob matches is not read, whichever way it spells the clock.")
        changed = code_problems(Inventory({**MIGRATIONS, "core/migrations/0001_first.py": MIGRATIONS["core/migrations/0001_first.py"] + "\n"}),
                                frozen_migrations=listed)
        self.assertEqual([problem.split(":")[0] for problem in changed if problem.startswith("P5")],
                         ["P5 core/migrations/0001_first.py"], "CLK-05 code half, Q26: a changed frozen migration must fail.")
        self.assertIn("this frozen migration is changed", changed[0], "CLK-05 code half, Q26: the failure must say the file changed.")
        missing = code_problems(Inventory({"core/migrations/0002_second.py": MIGRATIONS["core/migrations/0002_second.py"]}),
                                frozen_migrations=listed)
        self.assertEqual(len(missing), 1, "CLK-05 code half, Q26: a missing frozen migration must fail, and nothing else may.")
        self.assertIn("core/migrations/0001_first.py: this frozen migration is missing", missing[0],
                      "CLK-05 code half, Q26: the failure must say the file is missing.")
        unlisted = code_problems(frozen, frozen_migrations=listed[:1])
        self.assertEqual(len(unlisted), 1, "CLK-05 code half, Q26: a migration that is not listed is read as product code.")
        self.assertIn("core/migrations/0002_second.py:3, in forwards: P1 time.time", unlisted[0],
                      "CLK-05 code half, Q26: the unlisted migration's host read must be reported where it is written.")
        # Group 3: an interim test is not read while its blob matches, and is read once it changes.
        interim = Inventory({"tests/test_legacy.py": LEGACY})
        self.assertEqual(code_problems(interim, frozen_interim=[("tests/test_legacy.py", interim.blob("tests/test_legacy.py"))]), [],
                         "CLK-05 code half, group 3: an interim test file whose blob matches stays interim.")
        self.assertEqual(len(code_problems(interim, frozen_interim=[("tests/test_legacy.py", blob_id(b"as it was"))])), 1,
                         "CLK-05 code half, group 3: an interim test file that has changed is read, and its host read reported.")
        # Q27: two occurrences are reported twice, and cleared only by an entry that counts two in that enclosure.
        twice = Inventory({"core/diagnostic.py": DIAGNOSTIC})
        entry = ("core/diagnostic.py", "diagnostic", "SELECT now()", 2, "Q27: a diagnostic's two example strings")
        self.assertEqual(len(code_problems(twice)), 2, "CLK-05 code half, Q27: each of the two occurrences must be reported.")
        self.assertEqual(code_problems(twice, allowed=[entry]), [], "CLK-05 code half, Q27: an entry counting two clears both.")
        for wrong, why in ((entry[:3] + (1,) + entry[4:], "an entry counting one must not clear two"),
                           (entry[:1] + ("Other.diagnostic",) + entry[2:], "an entry for another enclosure clears nothing and matches nothing"),
                           (entry[:4] + ("",), "an entry without a reason fails")):
            self.assertNotEqual(code_problems(twice, allowed=[wrong]), [], "CLK-05 code half, Q27: %s." % why)

    def test_a_test_that_moves_the_clock_is_read_with_what_it_reaches(self):
        """CLK-05, code half, the reach. Each of the Advisor's cases Q17 to Q24 (rule section 7), and pass 1's cases
        A1 to A5, A10, A12 and A18 (rule v0.4), is judged by the
        check's own functions on a scratch inventory: which test files are selected, by a move trigger they hold or
        reach, and what is held against each, in its own text and in each definition it reaches, with the chain."""
        for name, files, interim, allowed, selected, records, words in GRAPH_CASES:
            inventory = Inventory(files)
            frozen = [(path, blob_id(was.encode("utf-8")) if was is not None else inventory.blob(path)) for path, was in interim]
            self.assertEqual(judge(inventory, allowed, frozen_interim=frozen), (selected, records),
                             "CLK-05 code half, case %s (%s): the files selected, or what is held against them, differ from "
                             "the verdict the agreed rule gives." % (name, words))
            if name == "Q24":
                self.assertNotIn("tools/sample_helpers.py", inventory.read,
                                 "CLK-05 code half, Q24: the edge into tools/ must end there; the file must not be read.")

    def test_the_inventory_is_read_from_a_plain_directory_without_git(self):
        """CLK-05, code half, the tested tree. The inventory is read from the files on disk of a plain directory that
        holds no ``.git`` (the route's snapshot): each ``*.py`` file by its path and the git blob id of its bytes; a
        ``__pycache__`` directory is never entered; a file that is not ``*.py`` is not read; a file that cannot be
        read, and a root that is no directory, fail (P5) and are never read as empty."""
        host_read = b"import time\n\n\ndef stamp():\n    return time.time()\n"
        frozen = b"from django.utils import timezone\n\nWHEN = timezone.now()\n"
        written = {
            "src/intevia/services/sample.py": host_read,
            "src/intevia/services/__pycache__/sample.py": host_read,
            "src/intevia/services/notes.txt": host_read,
            "core/migrations/0001_sample.py": frozen,
            "core/migrations/__pycache__/0001_sample.py": b"this is not Python (",
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "snapshot"
            for path, data in written.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(data)
            self.assertFalse((root / ".git").exists())
            try:
                inventory = tested_tree(root)
            except Unreadable as unreadable:
                self.fail("CLK-05 code half, the tested tree: a plain directory with no .git must be read from its files "
                          "on disk, as the route's snapshot is. It could not be read: %s" % unreadable)
            self.assertEqual(
                {path: inventory.blob(path) for path in inventory.files},
                {path: hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() for path, data in written.items()
                 if path.endswith(".py") and "__pycache__" not in path},
                "CLK-05 code half, the tested tree: the inventory must be exactly the *.py files on disk outside "
                "__pycache__, each under the git blob id of its own bytes.")
            listed = (("core/migrations/0001_sample.py", inventory.blob("core/migrations/0001_sample.py")),)
            self.assertEqual(
                [problem.split(". ")[0] for problem in code_problems(inventory, (), listed, ())],
                ["src/intevia/services/sample.py:5, in stamp: P1 time.time"],
                "CLK-05 code half, the tested tree: read from disk, the product file's host read must be reported once, "
                "the frozen migration matched by its blob, and nothing under __pycache__ read.")
            read_bytes = Path.read_bytes

            def refusing(path):
                if path.name == "sample.py":
                    raise PermissionError(13, "Permission denied", str(path))
                return read_bytes(path)

            with mock.patch.object(Path, "read_bytes", autospec=True, side_effect=refusing):
                with self.assertRaises(Unreadable, msg="CLK-05 code half, the tested tree (P5): a *.py file that cannot "
                                                       "be read must fail the reading, not be left out."):
                    tested_tree(root)
            with self.assertRaises(Unreadable, msg="CLK-05 code half, the tested tree (P5): a root that is no directory "
                                                   "must fail the reading, not give an empty inventory."):
                tested_tree(root / "absent")

    def test_python_reads_the_time_only_through_the_clock(self):
        """CLK-05, code half, the inventory. At the tested commit, no product file, no migration added after the frozen
        list, no interim test file changed since the baseline, and no test file that moves the clock (nor a definition
        it reaches) holds a written form the rule lists (P1 to P4), but the entries the Human Governor allows; each
        file the reading needs can be read (P5), and each frozen migration is present and unchanged."""
        try:
            inventory = tested_tree(Path(__file__).resolve().parent.parent)
        except Unreadable as unreadable:
            self.fail("CLK-05 code half (P5): the tested tree could not be read from disk, so nothing is shown: %s" % unreadable)
        self.assertGreater(sum(1 for path in inventory.files if group_of(path, inventory, {}, {}) == "product"), 0,
                           "CLK-05 code half: the tested commit must hold product files to read.")
        self.assertEqual(code_problems(inventory, ALLOWED, FROZEN_MIGRATIONS, FROZEN_INTERIM_TESTS), [],
                         "CLK-05 code half: Python must read the time only through the INTEVIA Clock.")
