#!/usr/bin/env python3
"""INTEVIA clock census v1.2 (US015-B-21, Vision Chamber) -- READ-ONLY.

Purpose: list, mechanically, every candidate place in the repository where a time is read from a
clock, supplied, assigned, stored or compared, so the Lead Designer's one-clock redesign (ledger
H61/H88; the Human Governor, 13 Oct 2026) works from a list and not from any seat's own search.
The census reports CANDIDATE OCCURRENCES. Counts do not establish distinct operational sites or
their clock provenance; unresolved sources and consumers stay explicit review items. Tracing a
time from its source to every rule that uses it is the Lead Designer's analysis, not this tool's.

v1.2 (the Advisor's review of v1.1, 13 Oct 2026): import aliases tracked per scope, with
shadowing, reassignment and clock functions stored in variables or parameter defaults; project
clock wrappers found and their calls linked; equality and both operands in comparisons; times
recognised by datetime annotations as well as names; localtime()/localdate() judged by their
real arguments; Django's database-clock functions (Now, TransactionNow, db_default); every
DateTimeField/DateField/TimeField declaration; time values supplied and assigned outside tests;
broader SQL comparison shapes; each database clock labelled with its sampling (transaction
start, statement start, or the instant); exact lines and an occurrence ID for every match;
runtime, migration, test, tooling and description kept apart; a repository-wide file inventory
with a reason for every exclusion; the candidate's identity and a before/after stability check;
an INCOMPLETE status, never a silent success; and a built-in self-test that must pass first.

What it reads: every Python, .sql, .html, .js and .ts file git knows (tracked and untracked,
.gitignore respected). PowerShell (.ps1/.psm1) is excluded by design and listed (ledger H83).
It never imports Django or the project, opens no database, reads no credential, and takes no time
from any clock: identity comes from git and file hashes.

What it writes: only a new output folder (refused if it exists): census.tsv, files.tsv,
inventory.tsv, errors.tsv, selftest.txt, summary.json.

Exit: 0 COMPLETE; 3 INCOMPLETE (output written; reasons in summary.json); 4 self-test failed (no
scan); 2 refused (bad arguments).

Usage (PowerShell 7):
  python CLOCK_CENSUS_v1_2.py --self-test
  python CLOCK_CENSUS_v1_2.py --repo "$HOME\\GitHub\\INTEVIA" --out "$HOME\\Downloads\\CLOCK_CENSUS_v1_2_<label>" [--state-sha256 <SE state hash>]

Final authority: Carmian Owen, Human Governor.
"""
import argparse
import ast
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

VERSION = "1.2"
TEXT_SUFFIXES = {".sql", ".html", ".js", ".ts"}
POWERSHELL_SUFFIXES = {".ps1", ".psm1", ".psd1"}
EXPECTED_ROOTS = ["src", "core", "intevia", "tests"]

HOST_PATHS = {
    "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.datetime.today",
    "datetime.date.today", "django.utils.timezone.now", "django.utils.timezone.localtime",
    "django.utils.timezone.localdate", "time.time", "time.time_ns", "time.localtime",
    "time.gmtime", "time.ctime", "time.asctime", "time.strftime",
}
# Functions that read the clock only when no time value is supplied: (positional index, keyword name).
VALUE_ARG = {
    "django.utils.timezone.localtime": (0, "value"), "django.utils.timezone.localdate": (0, "value"),
    "time.localtime": (0, "secs"), "time.gmtime": (0, "secs"), "time.ctime": (0, "secs"),
    "time.asctime": (0, "t"), "time.strftime": (1, "t"),
}
ELAPSED_PATHS = {"time.monotonic", "time.monotonic_ns", "time.perf_counter", "time.perf_counter_ns",
                 "time.process_time", "time.process_time_ns"}
UNRESOLVED_ATTRS = {"now", "utcnow", "today"}
WRAPPER_NAME_HINTS = {"now", "utcnow", "today", "current_time", "current_timestamp"}

TIME_NAME = re.compile(r"(^at$|_at$|_time$|^known_at$|^state_at$)")
DATETIME_ANN = re.compile(r"\b(datetime|date|time)\b")
ORM_LOOKUP = re.compile(r"(?:^|__)(?P<field>\w*_at)__(?P<op>lt|lte|gt|gte|range|exact|in|isnull|date|year|month|day)$")
ORM_PLAIN_TIME = re.compile(r"(?:^|__)\w*_at$")
FILTER_METHODS = {"filter", "exclude", "get", "aggregate", "annotate", "order_by", "values", "values_list"}
WRITE_METHODS = {"create", "update", "update_or_create", "get_or_create", "bulk_create", "save"}
FIELD_TYPES = {"DateTimeField", "DateField", "TimeField"}
VALIDATOR_HINTS = ("timestamp", "aware", "utc")

DB_CLOCKS = [
    (re.compile(r"\bclock_timestamp\s*\(\s*\)", re.I), "INSTANT"),
    (re.compile(r"\btimeofday\s*\(\s*\)", re.I), "INSTANT"),
    (re.compile(r"\bstatement_timestamp\s*\(\s*\)", re.I), "STATEMENT_START"),
    (re.compile(r"\btransaction_timestamp\s*\(\s*\)", re.I), "TRANSACTION_START"),
    (re.compile(r"\bnow\s*\(\s*\)", re.I), "TRANSACTION_START"),
    (re.compile(r"\bcurrent_timestamp\b(?:\s*\(\s*\d+\s*\))?", re.I), "TRANSACTION_START"),
    (re.compile(r"\blocaltimestamp\b", re.I), "TRANSACTION_START"),
    (re.compile(r"\bcurrent_date\b", re.I), "TRANSACTION_START"),
    (re.compile(r"\bcurrent_time\b", re.I), "TRANSACTION_START"),
    (re.compile(r"\blocaltime\b", re.I), "TRANSACTION_START"),
    (re.compile(r"\btimestamp(?:tz)?\s+'now'|'now'\s*::\s*\w+", re.I), "TRANSACTION_START"),
]
COL = r"(?:\"?\w+\"?\.)?\"?(?P<col>\w+_at)\"?"
CAST = r"(?:\s*::\s*\w+(?:\s+with(?:out)?\s+time\s+zone)?)?"
OP = r"(?P<op><=|>=|<>|!=|(?<![:<>!=])=(?![=>])|<(?![=>])|>(?!=))"
SQL_CMP_LEFT = re.compile(COL + CAST + r"\s*" + OP, re.I)
SQL_CMP_RIGHT = re.compile(OP + r"\s*" + COL, re.I)
SQL_BETWEEN = re.compile(COL + CAST + r"\s+(?:NOT\s+)?BETWEEN\b", re.I)
SQL_DISTINCT = re.compile(COL + CAST + r"\s+IS\s+(?:NOT\s+)?DISTINCT\s+FROM\b", re.I)
SQL_ASSIGN = [re.compile(r"\bNEW\.\"?(?P<col>\w+_at)\"?\s*:=", re.I),
              re.compile(r"\bSET\s+\"?(?P<col>\w+_at)\"?\s*=", re.I),
              re.compile(r",\s*\"?(?P<col>\w+_at)\"?\s*=\s*(?=[^,;]*\bWHERE\b)", re.I)]
SQL_INSERT = re.compile(r"\bINSERT\s+INTO\s+[\w.\"]+\s*\((?P<cols>[^)]*)\)", re.I)
SQL_COLUMN = re.compile(r"\"?(?P<col>\w+)\"?\s+timestamp(?:tz)?(?:\s*\(\s*\d\s*\))?(?:\s+with(?:out)?\s+time\s+zone)?\b", re.I)
TEMPLATE_NOW = re.compile(r"\{%\s*now\b")
JS_CLOCK = re.compile(r"\bDate\.now\s*\(|\bnew\s+Date\s*\(\s*\)")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def area_of(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    if rel.startswith("tests/") or "/tests/" in rel or name.startswith("test_") or name.endswith("_tests.py") or name == "tests.py":
        return "tests"
    if rel.startswith("verification/") or rel.startswith("scripts/"):
        return "tooling"
    if "/migrations/" in rel:
        return "migration"
    if rel.startswith(("src/", "core/", "intevia/", "templates/")) or name == "manage.py":
        return "runtime"
    return "other"


def module_of(rel: str) -> str:
    return rel[:-3].replace("/", ".") if rel.endswith(".py") else rel


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def docstring_ids(tree) -> set:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def sql_matches(text: str):
    """Yield (start, end, category, detail, semantics) for SQL in one string; no overlapping duplicates."""
    out, assign_spans = [], []
    for rx in SQL_ASSIGN:
        for m in rx.finditer(text):
            assign_spans.append((m.start(), m.end()))
            out.append((m.start(), m.end(), "SQL_TIME_ASSIGN", m.group(0).strip()[:160], ""))
    for m in SQL_INSERT.finditer(text):
        for col in re.findall(r"\b(\w+_at)\b", m.group("cols")):
            out.append((m.start(), m.end(), "SQL_TIME_ASSIGN", f"INSERT column {col}", ""))
    for rx, sem in DB_CLOCKS:
        for m in rx.finditer(text):
            out.append((m.start(), m.end(), "DB_CLOCK_SQL", m.group(0), sem))
    seen = set()
    for rx in (SQL_CMP_LEFT, SQL_CMP_RIGHT, SQL_BETWEEN, SQL_DISTINCT):
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in assign_spans):
                continue
            key = (m.group("col"), m.start() // 1)
            if key in seen:
                continue
            seen.add(key)
            out.append((m.start(), m.end(), "SQL_TIME_COMPARE", m.group(0).strip()[:160], ""))
    for m in SQL_COLUMN.finditer(text):
        out.append((m.start(), m.end(), "SQL_TIME_COLUMN", m.group(0).strip()[:120], ""))
    return out


class Census(ast.NodeVisitor):
    """One file. Pass 1 (wrappers=None) finds clock wrappers; pass 2 links calls to them."""

    def __init__(self, rel, area, lines, wrappers=None, method_wrappers=None):
        self.rel, self.area, self.lines = rel, area, lines
        self.module = module_of(rel)
        self.wrappers = wrappers or {}
        self.method_wrappers = method_wrappers or {}
        self.scopes = [{}]          # alias name -> dotted path; innermost last
        self.typed = [set()]        # names annotated with a datetime type, per scope
        self.names = []             # display scope
        self.classes = []
        self.findings = []
        self.called = set()
        self.doc_ids = set()
        self.module_funcs = set()

    # ---------- infrastructure ----------
    @property
    def aliases(self):
        return self.scopes[-1]

    def add(self, node, category, detail, semantics="", line=None, exact="yes", col=None, kind="code"):
        line = line if line is not None else getattr(node, "lineno", 0)
        col = col if col is not None else getattr(node, "col_offset", 0)
        src = self.lines[line - 1].strip() if 0 < line <= len(self.lines) else ""
        self.findings.append({
            "occ_id": f"{self.rel}:{line}:{col}:{category}:{len(self.findings)}",
            "area": self.area, "text_kind": kind, "path": self.rel, "line": line, "line_exact": exact,
            "col": col, "category": category, "detail": detail, "semantics": semantics,
            "scope": ".".join(self.names) or "<module>", "source": src[:240],
        })

    def resolve(self, node):
        text = dotted(node)
        if text is None:
            return None
        head, _, rest = text.partition(".")
        base, found = None, False
        for scope in reversed(self.scopes):
            if head in scope:
                base, found = scope[head], True
                break
        if found and base is None:
            return None  # shadowed by a parameter or a reassignment
        if base is None:
            if head in self.module_funcs:
                base = f"{self.module}.{head}"
            else:
                return None
        return base + ("." + rest if rest else "")

    def is_time_expr(self, node) -> bool:
        text = ast.unparse(node)
        if re.search(r"\w_at\b", text):
            return True
        return isinstance(node, ast.Name) and any(node.id in t for t in self.typed)

    def wrapper_for(self, path):
        if not path:
            return None
        if path in self.wrappers:
            return path, self.wrappers[path]
        mod, _, func = path.rpartition(".")
        last = mod.rsplit(".", 1)[-1]
        for key, kind in self.wrappers.items():
            kmod, _, kfunc = key.rpartition(".")
            if kfunc == func and kmod.rsplit(".", 1)[-1] == last:
                return key, kind
        return None

    # ---------- module ----------
    def visit_Module(self, node):
        self.doc_ids = docstring_ids(node)
        self.module_funcs = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.generic_visit(node)

    # ---------- imports (scoped) ----------
    def visit_Import(self, node):
        for a in node.names:
            if a.asname:
                self.aliases[a.asname] = a.name
            else:
                self.aliases[a.name.split(".")[0]] = a.name.split(".")[0]

    def visit_ImportFrom(self, node):
        mod = node.module or ""
        if node.level:
            mod = ".".join(self.module.split(".")[: -node.level] + ([mod] if mod else []))
        for a in node.names:
            if a.name != "*":
                self.aliases[a.asname or a.name] = f"{mod}.{a.name}" if mod else a.name

    # ---------- scopes ----------
    def visit_ClassDef(self, node):
        for d in node.decorator_list + node.bases + node.keywords:
            self.visit(d)
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                ann = ast.unparse(stmt.annotation)
                if TIME_NAME.search(stmt.target.id) or DATETIME_ANN.search(ann):
                    if self.area in ("runtime", "migration", "other"):
                        self.names.append(node.name)
                        self.add(stmt, "CALLER_TIME_FIELD", f"{node.name}.{stmt.target.id}: {ann}"[:160])
                        self.names.pop()
        self.scopes.append(dict(self.aliases))
        self.typed.append(set())
        self.names.append(node.name)
        self.classes.append(node.name)
        for stmt in node.body:
            self.visit(stmt)
        self.classes.pop()
        self.names.pop()
        self.typed.pop()
        self.scopes.pop()

    def visit_FunctionDef(self, node):
        for d in node.decorator_list:
            self.visit(d)
        args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        defaults = [None] * (len(node.args.posonlyargs + node.args.args) - len(node.args.defaults)) + list(node.args.defaults) + list(node.args.kw_defaults)
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(d)
        inner = dict(self.aliases)
        typed = set()
        self.names.append(node.name)
        for a, d in zip(args, defaults):
            inner[a.arg] = None  # a parameter shadows any outer binding
            ann = ast.unparse(a.annotation) if a.annotation else ""
            dpath = self.resolve(d) if d is not None and isinstance(d, (ast.Name, ast.Attribute)) else None
            if dpath in HOST_PATHS:
                inner[a.arg] = dpath
            if ann and DATETIME_ANN.search(ann):
                typed.add(a.arg)
            if self.area in ("runtime", "migration", "other"):
                if TIME_NAME.search(a.arg) or (ann and DATETIME_ANN.search(ann) and "Callable" not in ann):
                    self.add(a, "CALLER_TIME_FIELD", f"parameter {a.arg}: {ann}".strip(": ")[:160])
            if "clock" in a.arg.lower():
                self.add(a, "INJECTED_CLOCK", f"parameter {a.arg}" + (f" default {ast.unparse(d)}" if d is not None else ""))
        self.scopes.append(inner)
        self.typed.append(typed)
        for stmt in node.body:
            self.visit(stmt)
        self.typed.pop()
        self.scopes.pop()
        self.names.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        inner = dict(self.aliases)
        inner.update({a.arg: None for a in node.args.args})
        self.scopes.append(inner)
        self.visit(node.body)
        self.scopes.pop()

    # ---------- assignments ----------
    def _target_name(self, t):
        if isinstance(t, ast.Name):
            return t.id
        if isinstance(t, ast.Attribute):
            return t.attr
        if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
            return t.slice.value
        return None

    def visit_Assign(self, node):
        self.visit(node.value)
        vpath = self.resolve(node.value) if isinstance(node.value, (ast.Name, ast.Attribute)) else None
        for t in node.targets:
            if isinstance(t, ast.Name):
                if t.id in ("TIME_ZONE", "USE_TZ"):
                    self.add(node, "SETTINGS_TIME", ast.unparse(node)[:160])
                if vpath in HOST_PATHS:
                    self.aliases[t.id] = vpath
                elif any(t.id in sc for sc in self.scopes):
                    self.aliases[t.id] = None  # reassigned: no longer the imported binding
            name = self._target_name(t)
            if name and TIME_NAME.search(name) and self.area not in ("tests",):
                self.add(node, "TIME_ASSIGNED", f"{ast.unparse(t)} = {ast.unparse(node.value)}"[:200])
            self.visit(t)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)
        if isinstance(node.target, ast.Name):
            ann = ast.unparse(node.annotation)
            if DATETIME_ANN.search(ann):
                self.typed[-1].add(node.target.id)
            if node.value is not None and TIME_NAME.search(node.target.id) and self.area != "tests" and not self.classes[-1:] == [self.names[-1] if self.names else None]:
                self.add(node, "TIME_ASSIGNED", f"{node.target.id} = {ast.unparse(node.value)}"[:200])

    def visit_Dict(self, node):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str) and TIME_NAME.search(k.value) and self.area != "tests":
                self.add(k, "TIME_ASSIGNED", f"key {k.value!r}: {ast.unparse(v)}"[:200])
        self.generic_visit(node)

    # ---------- calls ----------
    def visit_Call(self, node):
        self.called.add(id(node.func))
        path = self.resolve(node.func)
        fname = dotted(node.func) or ""
        last = fname.split(".")[-1]
        lower = last.lower()

        if path in HOST_PATHS:
            if path in VALUE_ARG:
                idx, kwname = VALUE_ARG[path]
                supplied = node.args[idx] if len(node.args) > idx else next((k.value for k in node.keywords if k.arg == kwname), None)
                if supplied is None or (isinstance(supplied, ast.Constant) and supplied.value is None):
                    self.add(node, "HOST_CLOCK_CALL", f"{path} (no time value supplied)")
                elif not isinstance(supplied, ast.Constant):
                    self.add(node, "HOST_CLOCK_POSSIBLE", f"{path}({ast.unparse(supplied)[:60]}): reads now if that value is None")
            else:
                via = "" if dotted(node.func) and dotted(node.func).split(".")[0] in ("datetime", "dt", "timezone", "time") else f" via {fname}"
                self.add(node, "HOST_CLOCK_CALL", path + via)
        elif path in ELAPSED_PATHS:
            self.add(node, "ELAPSED_TIMER", path)
        elif path and path.startswith("django.") and last in ("Now", "TransactionNow"):
            self.add(node, "DB_CLOCK_ORM", path, "TRANSACTION_START" if last == "TransactionNow" else "DJANGO_NOW: confirm the PostgreSQL function for the installed Django")
        elif path is None and isinstance(node.func, ast.Attribute) and node.func.attr in UNRESOLVED_ATTRS:
            self.add(node, "HOST_CLOCK_UNRESOLVED", fname)
        elif path and path.rsplit(".", 1)[-1] in WRAPPER_NAME_HINTS and not path.startswith(("datetime.", "time.", "django.")) and not self.wrapper_for(path):
            self.add(node, "HOST_CLOCK_UNRESOLVED", f"{fname} resolves to {path}: a project function named like a clock")

        w = self.wrapper_for(path)
        if w:
            self.add(node, "CLOCK_WRAPPER_CALL", f"{w[0]} ({w[1]} clock)")
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "self" and self.classes:
            mk = (self.module, self.classes[-1], node.func.attr)
            if mk in self.method_wrappers:
                self.add(node, "CLOCK_WRAPPER_CALL", f"{self.classes[-1]}.{node.func.attr} ({self.method_wrappers[mk]} clock)")
        if "clock" in lower:
            self.add(node, "INJECTED_CLOCK", f"call {fname}()")
        if ("validate" in lower and ("time" in lower or "timestamp" in lower)) or any(h in lower for h in VALIDATOR_HINTS):
            self.add(node, "CALLER_TIME_ACCEPTED", f"call {fname}()")
        if fname in ("max", "min") and any(self.is_time_expr(a) for a in node.args):
            self.add(node, "PY_TIME_MAX_MIN", ast.unparse(node)[:200])
        if last == "F" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) and ORM_PLAIN_TIME.search(node.args[0].value):
            self.add(node, "ORM_TIME_EXPRESSION", ast.unparse(node)[:160])
        if last in FIELD_TYPES:
            opts = ", ".join(f"{k.arg}={ast.unparse(k.value)}" for k in node.keywords if k.arg in ("auto_now", "auto_now_add", "default", "db_default", "null"))
            self.add(node, "TIME_FIELD_DECL", f"{last}({opts})"[:200])

        method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        for kw in node.keywords:
            if kw.arg is None:
                if isinstance(kw.value, ast.Dict) and any(isinstance(k, ast.Constant) and isinstance(k.value, str) and ORM_PLAIN_TIME.search(k.value.split("__")[0] if "__" in k.value else k.value) for k in kw.value.keys):
                    self.add(node, "ORM_TIME_EXPRESSION", f"**{ast.unparse(kw.value)[:150]}")
                continue
            line = getattr(kw, "lineno", None)
            m = ORM_LOOKUP.search(kw.arg)
            if m:
                self.add(node, "ORM_TIME_FILTER", f"{kw.arg} = {ast.unparse(kw.value)[:160]}", line=line)
            elif ORM_PLAIN_TIME.search(kw.arg):
                if method in FILTER_METHODS or last == "Q":
                    self.add(node, "ORM_TIME_FILTER", f"{kw.arg} = {ast.unparse(kw.value)[:160]} (equality)", line=line)
                elif self.area != "tests" and last not in FIELD_TYPES:
                    self.add(node, "TIME_SUPPLIED", f"{last}({kw.arg}={ast.unparse(kw.value)[:140]})", line=line)
            if kw.arg in ("auto_now", "auto_now_add") and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                self.add(node, "MODEL_AUTO_TIME", f"{fname}({kw.arg}=True)")
            if "clock" in kw.arg.lower():
                self.add(node, "INJECTED_CLOCK", f"{kw.arg}={ast.unparse(kw.value)[:120]}", line=line)
        self.generic_visit(node)

    # ---------- bare references ----------
    def visit_Attribute(self, node):
        if id(node) not in self.called:
            path = self.resolve(node)
            if path in HOST_PATHS:
                self.add(node, "HOST_CLOCK_REFERENCE", path)
        self.generic_visit(node)

    def visit_Name(self, node):
        if id(node) not in self.called and isinstance(node.ctx, ast.Load):
            path = self.resolve(node)
            if path in HOST_PATHS:
                self.add(node, "HOST_CLOCK_REFERENCE", path)

    # ---------- comparisons ----------
    def visit_Compare(self, node):
        ops = [type(o).__name__ for o in node.ops]
        if any(o in ("Lt", "LtE", "Gt", "GtE", "Eq", "NotEq") for o in ops):
            if any(self.is_time_expr(x) for x in [node.left] + node.comparators):
                self.add(node, "PY_TIME_COMPARE", f"{ast.unparse(node)[:180]} [{','.join(ops)}]")
        self.generic_visit(node)

    # ---------- strings ----------
    def _scan(self, node, text, base_line, exact):
        kind = "docstring" if id(node) in self.doc_ids else "code"
        for start, _end, cat, detail, sem in sql_matches(text):
            nl = text.count("\n", 0, start)
            line = base_line + nl
            col = start - (text.rfind("\n", 0, start) + 1)
            self.add(node, cat, detail, sem, line=line, exact=exact, col=col, kind=kind)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and id(node.value) not in self.doc_ids:
            text = node.value.value
            exact = "yes" if (node.value.end_lineno - node.value.lineno) == text.count("\n") else "approx"
            for start, _e, cat, detail, sem in sql_matches(text):
                self.add(node.value, cat, detail, sem, line=node.value.lineno + text.count("\n", 0, start), exact=exact, kind="description")
            return
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and len(node.value) > 3:
            text = node.value
            exact = "yes" if (getattr(node, "end_lineno", node.lineno) - node.lineno) == text.count("\n") else "approx"
            self._scan(node, text, node.lineno, exact)

    def visit_JoinedStr(self, node):
        joined, spans = "", []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                s = len(joined)
                joined += v.value
                spans.append((s, len(joined), v))
                exact = "yes" if (getattr(v, "end_lineno", v.lineno) - v.lineno) == v.value.count("\n") else "approx"
                self._scan(v, v.value, v.lineno, exact)
            else:
                joined += "{}"
                self.visit(v.value if isinstance(v, ast.FormattedValue) else v)
        for start, end, cat, detail, sem in sql_matches(joined):
            if not any(a <= start and end <= b for a, b, _ in spans):
                self.add(node, cat, detail + " (spans an interpolation)", sem, exact="approx")


def find_wrappers(census: Census):
    """Top-level functions and methods whose own body reads a clock: their module paths and kinds."""
    funcs, methods = {}, {}
    for f in census.findings:
        if f["text_kind"] != "code":
            continue
        kind = "HOST" if f["category"] == "HOST_CLOCK_CALL" else ("DB" if f["category"] in ("DB_CLOCK_SQL", "DB_CLOCK_ORM") else None)
        if not kind:
            continue
        parts = f["scope"].split(".") if f["scope"] != "<module>" else []
        if len(parts) == 1 and parts[0] in census.module_funcs:
            funcs.setdefault(f"{census.module}.{parts[0]}", kind)
        elif len(parts) == 2:
            methods.setdefault((census.module, parts[0], parts[1]), kind)
    return funcs, methods


def scan_python(rel, text, wrappers=None, method_wrappers=None):
    tree = ast.parse(text, filename=rel)
    c = Census(rel, area_of(rel), text.splitlines(), wrappers, method_wrappers)
    c.visit(tree)
    return c


def scan_text(rel, text):
    lines = text.splitlines()
    area = area_of(rel)
    out = []

    def add(start, cat, detail, sem=""):
        line = text.count("\n", 0, start) + 1
        col = start - (text.rfind("\n", 0, start) + 1)
        src = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        out.append({"occ_id": f"{rel}:{line}:{col}:{cat}:{len(out)}", "area": area, "text_kind": "code", "path": rel,
                    "line": line, "line_exact": "yes", "col": col, "category": cat, "detail": detail,
                    "semantics": sem, "scope": "<text>", "source": src[:240]})

    suffix = Path(rel).suffix.lower()
    if suffix == ".sql":
        for start, _e, cat, detail, sem in sql_matches(text):
            add(start, cat, detail, sem)
    if suffix == ".html":
        for m in TEMPLATE_NOW.finditer(text):
            add(m.start(), "TEMPLATE_NOW", m.group(0))
    if suffix in (".js", ".ts", ".html"):
        for m in JS_CLOCK.finditer(text):
            add(m.start(), "JS_CLOCK", m.group(0))
    return out


# ------------------------------------------------------------------ self-test
SELF_TEST = {
    "src/a.py": '''"""Docstring mentioning clock_timestamp() is description, not a reading."""
import datetime
from django.utils import timezone
from django.db.models.functions import Now

def now_wrapper():
    return timezone.now()

def use(at: datetime.datetime, deadline: datetime.datetime, zone=None):
    a = timezone.now()
    b = timezone.localtime(timezone=zone)
    c = timezone.localtime(at)
    stamp = timezone.now
    d = stamp()
    e = now_wrapper()
    if deadline == at:
        pass
    if a.effective_at == a.received_at:
        pass
    rows = Thing.objects.filter(recorded_at__lte=a, received_at=b)
    Thing.objects.create(received_at=a, db_now=Now())
    def inner():
        from time import time as now
        return now()
    x = dict(recorded_at=a)
    return {"occurred_at": a}

class Command:
    received_at: datetime.datetime
    when: datetime.datetime

def shadow(timezone):
    return timezone.now()

class Model:
    created_at = models.DateTimeField(auto_now_add=True)
    expires = models.DateTimeField(null=True)
''',
    "core/migrations/0099_x.py": '''SQL = f"""
CREATE FUNCTION f() RETURNS trigger AS $$
BEGIN
    NEW.recorded_at := statement_timestamp();
    IF NEW.occurred_at > NEW.received_at THEN RAISE EXCEPTION 'x'; END IF;
    IF clock_timestamp() >= NEW.effective_at THEN RETURN NEW; END IF;
    PERFORM 1 WHERE known_at BETWEEN a AND b;
END $$ LANGUAGE plpgsql;
"""
''',
    "tests/support.py": '''from django.db import connection
def database_now():
    with connection.cursor() as c:
        c.execute("SELECT clock_timestamp()")
        return c.fetchone()[0]
''',
    "tests/test_b.py": '''from tests.support import database_now
def test_x():
    t = database_now()
''',
    "core/q.sql": "UPDATE t SET updated_at = now() WHERE created_at < recorded_at;\n",
    "core/templates/t.html": '<p>{% now "Y" %}</p><script>let t = Date.now();</script>\n',
}
SELF_TEST_EXPECT = [
    ("src/a.py", 10, "HOST_CLOCK_CALL"),             # timezone.now()
    ("src/a.py", 11, "HOST_CLOCK_CALL"),             # localtime(timezone=zone) still reads now
    ("src/a.py", 13, "HOST_CLOCK_REFERENCE"),        # stamp = timezone.now
    ("src/a.py", 14, "HOST_CLOCK_CALL"),             # stamp() via alias
    ("src/a.py", 15, "CLOCK_WRAPPER_CALL"),          # now_wrapper()
    ("src/a.py", 16, "PY_TIME_COMPARE"),             # deadline == at (typed, not *_at)
    ("src/a.py", 18, "PY_TIME_COMPARE"),             # equality on *_at
    ("src/a.py", 20, "ORM_TIME_FILTER"),             # recorded_at__lte
    ("src/a.py", 21, "TIME_SUPPLIED"),               # create(received_at=...)
    ("src/a.py", 21, "DB_CLOCK_ORM"),                # Now()
    ("src/a.py", 24, "HOST_CLOCK_CALL"),             # local import alias now() -> time.time
    ("src/a.py", 26, "TIME_ASSIGNED"),               # dict key occurred_at (line of the key)
    ("src/a.py", 29, "CALLER_TIME_FIELD"),           # received_at field
    ("src/a.py", 30, "CALLER_TIME_FIELD"),           # when: datetime (typed, not named)
    ("src/a.py", 36, "MODEL_AUTO_TIME"),
    ("src/a.py", 36, "TIME_FIELD_DECL"),
    ("src/a.py", 37, "TIME_FIELD_DECL"),             # expires: no auto flag, still listed
    ("core/migrations/0099_x.py", 4, "SQL_TIME_ASSIGN"),
    ("core/migrations/0099_x.py", 4, "DB_CLOCK_SQL"),
    ("core/migrations/0099_x.py", 5, "SQL_TIME_COMPARE"),
    ("core/migrations/0099_x.py", 6, "DB_CLOCK_SQL"),
    ("core/migrations/0099_x.py", 6, "SQL_TIME_COMPARE"),  # reversed operand
    ("core/migrations/0099_x.py", 7, "SQL_TIME_COMPARE"),  # BETWEEN
    ("tests/test_b.py", 3, "CLOCK_WRAPPER_CALL"),    # database_now() -> DB wrapper
    ("core/q.sql", 1, "DB_CLOCK_SQL"),
    ("core/q.sql", 1, "SQL_TIME_COMPARE"),
    ("core/templates/t.html", 1, "TEMPLATE_NOW"),
    ("core/templates/t.html", 1, "JS_CLOCK"),
]
SELF_TEST_FORBID = [
    ("src/a.py", 12, "HOST_CLOCK_CALL"),             # localtime(at) converts a supplied value
    ("src/a.py", 33, "HOST_CLOCK_CALL"),             # parameter named timezone shadows the import
]


def run_self_test():
    lines, ok = [], True
    pyfiles = {k: v for k, v in SELF_TEST.items() if k.endswith(".py")}
    passes = {k: scan_python(k, v) for k, v in pyfiles.items()}
    wrappers, methods = {}, {}
    for c in passes.values():
        f, m = find_wrappers(c)
        wrappers.update(f)
        methods.update(m)
    found = []
    for k, v in pyfiles.items():
        found.extend(scan_python(k, v, wrappers, methods).findings)
    for k, v in SELF_TEST.items():
        if not k.endswith(".py"):
            found.extend(scan_text(k, v))
    have = {(f["path"], f["line"], f["category"]) for f in found}
    for e in SELF_TEST_EXPECT:
        hit = e in have
        ok &= hit
        lines.append(f"{'PASS' if hit else 'FAIL'} expect {e}")
    for e in SELF_TEST_FORBID:
        hit = e in have
        ok &= not hit
        lines.append(f"{'PASS' if not hit else 'FAIL'} forbid {e}")
    doc = [f for f in found if f["path"] == "src/a.py" and f["line"] == 1 and f["category"] == "DB_CLOCK_SQL"]
    dok = bool(doc) and all(f["text_kind"] == "docstring" for f in doc)
    ok &= dok
    lines.append(f"{'PASS' if dok else 'FAIL'} docstring match labelled 'docstring'")
    occ = [f["occ_id"] for f in found]
    uok = len(occ) == len(set(occ))
    ok &= uok
    lines.append(f"{'PASS' if uok else 'FAIL'} every occurrence ID unique")
    lines.append(f"SELF_TEST={'PASS' if ok else 'FAIL'} ({sum(l.startswith('PASS') for l in lines)} of {len(lines)} checks)")
    return ok, lines


# ------------------------------------------------------------------ repository run
def git(repo: Path, *args: str, raw=False):
    out = subprocess.run(["git", "-C", str(repo), "--no-optional-locks", *args], capture_output=True, check=False)
    if out.returncode != 0:
        return None
    return out.stdout if raw else out.stdout.decode("utf-8", "replace")


def identity(repo: Path, state_sha256):
    head = (git(repo, "rev-parse", "HEAD") or "").strip()
    branch = (git(repo, "branch", "--show-current") or "").strip()
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all") or ""
    diff = git(repo, "diff", "--binary", head, raw=True) if head else None
    untracked = [p for p in (git(repo, "ls-files", "--others", "--exclude-standard") or "").splitlines() if p]
    rows = sorted(f"{sha256_bytes((repo / p).read_bytes())}:{p}" for p in untracked if (repo / p).is_file())
    ident = {
        "head": head, "branch": branch, "status_paths": sorted(l[3:] for l in status.splitlines() if l.strip()),
        "diff_sha256": sha256_bytes(diff) if diff is not None else None, "untracked_rows": rows,
    }
    if state_sha256 and head and diff is not None:
        parts = [f"state_sha256={state_sha256}", f"baseline={head}", f"diff={ident['diff_sha256']}"] + rows
        ident["candidate_binding"] = sha256_bytes("\n".join(parts).encode("utf-8"))
    return ident


def main() -> int:
    ap = argparse.ArgumentParser(description="INTEVIA clock census v1.2 (read-only)")
    ap.add_argument("--repo")
    ap.add_argument("--out")
    ap.add_argument("--state-sha256", default=None)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    ok, st_lines = run_self_test()
    for l in st_lines[-1:]:
        print(l)
    if a.self_test:
        print("\n".join(st_lines))
        return 0 if ok else 4
    if not ok:
        print("REFUSED: the self-test failed; no scan was made.")
        print("\n".join(st_lines))
        return 4
    if not a.repo or not a.out:
        print("REFUSED: --repo and --out are required (or use --self-test)")
        return 2
    repo, out = Path(a.repo).resolve(), Path(a.out).resolve()
    if not (repo / ".git").exists():
        print(f"REFUSED: not a git repository: {repo}")
        return 2
    if out.exists():
        print(f"REFUSED: output folder already exists: {out}")
        return 2
    out.mkdir(parents=True)
    (out / "selftest.txt").write_text("\n".join(st_lines) + "\n", encoding="utf-8")

    reasons = []
    before = identity(repo, a.state_sha256)
    if not before["head"]:
        reasons.append("git identity unavailable")
    tracked = [p for p in (git(repo, "ls-files") or "").splitlines() if p]
    untracked = [p for p in (git(repo, "ls-files", "--others", "--exclude-standard") or "").splitlines() if p]
    for root in EXPECTED_ROOTS:
        if not (repo / root).is_dir():
            reasons.append(f"expected root missing: {root}/")

    inventory, files, errors, findings = [], [], [], []
    to_scan = []
    for rel, origin in [(p, "tracked") for p in tracked] + [(p, "untracked") for p in untracked]:
        p = repo / rel
        suffix = Path(rel).suffix.lower()
        if not p.is_file():
            inventory.append({"path": rel, "origin": origin, "included": "no", "reason": "listed by git but absent from the working tree"})
            continue
        if suffix == ".py":
            inventory.append({"path": rel, "origin": origin, "included": "yes", "reason": "python"})
            to_scan.append((rel, "python"))
        elif suffix in TEXT_SUFFIXES:
            inventory.append({"path": rel, "origin": origin, "included": "yes", "reason": f"text ({suffix})"})
            to_scan.append((rel, "text"))
        elif suffix in POWERSHELL_SUFFIXES:
            inventory.append({"path": rel, "origin": origin, "included": "no", "reason": "PowerShell tooling: excluded by design (ledger H83)"})
        else:
            inventory.append({"path": rel, "origin": origin, "included": "no", "reason": f"not a scanned type ({suffix or 'no suffix'})"})

    texts, hashes = {}, {}
    for rel, kind in to_scan:
        data = (repo / rel).read_bytes()
        hashes[rel] = sha256_bytes(data)
        try:
            texts[rel] = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            errors.append({"path": rel, "error": f"not UTF-8 at byte {exc.start}"})
            texts[rel] = None

    passes = {}
    for rel, kind in to_scan:
        if kind == "python" and texts[rel] is not None:
            try:
                passes[rel] = scan_python(rel, texts[rel])
            except SyntaxError as exc:
                errors.append({"path": rel, "error": f"SyntaxError line {exc.lineno}: {exc.msg}"})
    wrappers, methods = {}, {}
    for c in passes.values():
        f, m = find_wrappers(c)
        wrappers.update(f)
        methods.update(m)
    defined = [{"occ_id": f"{k}:wrapper", "area": area_of(k.rsplit('.', 1)[0].replace('.', '/') + '.py'), "text_kind": "code",
                "path": k.rsplit(".", 1)[0].replace(".", "/") + ".py", "line": 0, "line_exact": "n/a", "col": 0,
                "category": "CLOCK_WRAPPER_DEFINED", "detail": f"{k} ({v} clock)", "semantics": "", "scope": k.rsplit(".", 1)[1], "source": ""}
               for k, v in sorted(wrappers.items())]
    defined += [{"occ_id": f"{m}.{c}.{f}:wrapper", "area": area_of(m.replace('.', '/') + '.py'), "text_kind": "code",
                 "path": m.replace(".", "/") + ".py", "line": 0, "line_exact": "n/a", "col": 0, "category": "CLOCK_WRAPPER_DEFINED",
                 "detail": f"{c}.{f} ({v} clock)", "semantics": "", "scope": f"{c}.{f}", "source": ""}
                for (m, c, f), v in sorted(methods.items())]
    findings.extend(defined)

    for rel, kind in to_scan:
        n = 0
        if texts.get(rel) is None:
            files.append({"area": area_of(rel), "path": rel, "sha256": hashes[rel], "bytes": len((repo / rel).read_bytes()), "findings": "DECODE_ERROR"})
            continue
        if kind == "python":
            if rel not in passes:
                files.append({"area": area_of(rel), "path": rel, "sha256": hashes[rel], "bytes": len(texts[rel]), "findings": "PARSE_ERROR"})
                continue
            res = scan_python(rel, texts[rel], wrappers, methods).findings
        else:
            res = scan_text(rel, texts[rel])
        findings.extend(res)
        files.append({"area": area_of(rel), "path": rel, "sha256": hashes[rel], "bytes": len(texts[rel].encode("utf-8")), "findings": len(res)})

    changed = [rel for rel, _ in to_scan if (repo / rel).is_file() and sha256_bytes((repo / rel).read_bytes()) != hashes[rel]]
    after = identity(repo, a.state_sha256)
    stable = not changed and before == after
    if errors:
        reasons.append(f"{len(errors)} file(s) could not be read or parsed")
    if not stable:
        reasons.append(f"the candidate changed during the census ({len(changed)} scanned file(s) changed)")
    status = "COMPLETE" if not reasons else "INCOMPLETE"

    def write_tsv(name, rows, cols):
        with open(out / name, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    cols = ["occ_id", "area", "text_kind", "path", "line", "line_exact", "col", "category", "detail", "semantics", "scope", "source"]
    findings.sort(key=lambda r: (r["area"], r["path"], int(r["line"]), int(r["col"]), r["category"]))
    write_tsv("census.tsv", findings, cols)
    write_tsv("files.tsv", files, ["area", "path", "sha256", "bytes", "findings"])
    write_tsv("inventory.tsv", inventory, ["path", "origin", "included", "reason"])
    write_tsv("errors.tsv", errors, ["path", "error"])

    operational = Counter(r["category"] for r in findings if r["area"] == "runtime" and r["text_kind"] == "code")
    summary = {
        "census_version": VERSION,
        "census_script_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "status": status, "incomplete_reasons": reasons, "self_test": "PASS",
        "repo": str(repo), "identity_before": before, "stable": stable, "changed_during_scan": changed,
        "files_in_inventory": len(inventory), "files_scanned": len(files), "errors": len(errors),
        "findings_total": len(findings),
        "by_category": dict(sorted(Counter(r["category"] for r in findings).items())),
        "operational_runtime_code_by_category": dict(sorted(operational.items())),
        "by_category_area_kind": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in sorted(Counter((r["category"], r["area"], r["text_kind"]) for r in findings).items())},
        "db_clock_sampling": dict(sorted(Counter(r["semantics"] for r in findings if r["category"] in ("DB_CLOCK_SQL", "DB_CLOCK_ORM")).items())),
        "limits": [
            "Candidate occurrences, not established sites; tracing source to consumer is the Lead Designer's analysis.",
            "Clocks read by Django or libraries on the project's behalf are outside repository code.",
            "Positional SQL parameters and values passed through containers are not traced.",
            "PowerShell tooling is excluded by design (inventory.tsv lists it).",
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"CENSUS_STATUS={status}")
    for r in reasons:
        print(f"INCOMPLETE_REASON={r}")
    if before.get("candidate_binding"):
        print(f"CANDIDATE_BINDING={before['candidate_binding']}")
    print(f"CENSUS_FILES={len(files)}")
    print(f"CENSUS_FINDINGS={len(findings)}")
    for cat, n in summary["by_category"].items():
        print(f"{cat}={n}")
    print(f"CENSUS_OUT={out}")
    return 0 if status == "COMPLETE" else 3


if __name__ == "__main__":
    sys.exit(main())
