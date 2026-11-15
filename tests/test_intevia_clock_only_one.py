"""CLK-05, the catalogue half: there is only one clock.

After the storage conversion (core migrations 0097 to 0103) nothing the database holds reads
the time except through the INTEVIA Clock's three faces. This is the standing check: a later
unit that installs SQL that reads the time fails here and is sent to a face.

CLK-05 reports what is written, with no exemption (the CK-2R9 acceptance rule v2.1, the Human
Governor's decision of 14 Nov 2026). The texts are read with PostgreSQL's own parser (libpg_query,
PostgreSQL 17, through the verification-only dependency ``pgparse``), and a read is what the parse
tree holds, wherever its value goes: (F1) a call of one of the five time functions by name
(unqualified or in ``pg_catalog``); (F2) an SQL time keyword; (F3) inside a string constant's value,
a match of the landed census tool's patterns (``verification.clock.clock_census_v1_2.DB_CLOCKS``);
(F4) a string constant holding a clock word (``now``, ``today``, ``tomorrow`` or ``yesterday``, in
any ASCII letter case, with no ASCII letter directly before or after it), in any quoting, with no
server or setting consulted; (F5) ``age`` called with one argument; (F6) any use of the type timetz, or of a domain
over it: a cast in either form, a type named in a statement, a PL/pgSQL variable, a column, a
routine's argument or return, a type built on it; and (F7) a text the parser cannot read, whatever
object holds it, a face's included. A type name is the one the catalogue resolves under the
routine's own search path, through its domains (a SECURITY DEFINER routine runs as its owner, so
its ``$user`` is the owner), and resolving a name never changes the reading session's search path.
CLK-05 reads routines, column defaults, views, constraints, trigger conditions, indexes, domain
defaults, row-security policies, rewrite rules, aggregate initial values and publication row
filters. A finding is cleared only by an exact entry on the allow-list (``ALLOWED``), each added
with the Human Governor's approval.

CK-2R10 added four guarantees, each named here with the function that holds it. (a) An object that
is a member of an extension is not read, in each of the places listed above (``_not_extension``).
(b) F6 follows a PL/pgSQL ``%TYPE`` declaration to the variable or column it names, and an anchor
the catalogue cannot resolve is reported unreadable (``_declared_timetz``). (c) The string constants
F3 and F4 read are taken from the text itself, as PostgreSQL's lexer reads it (``_string_literals``),
and a text is reported unreadable where a constant the parse tree holds, or a RAISE message,
disagrees with that reading (``_literals_agree``, ``written_strings``). (d) A face is excused only
for its own built-in, and a routine of the moving part only for a clock read by function or keyword
(F1, F2); a finding of another kind on either is reported as it is elsewhere (``_excused``).

CK-2j1a corrected three readings of that lexer and of ``%TYPE`` (owed ledger C1-130, item 9): a
``--`` comment ends at a carriage return as at a newline; a ``%TYPE`` anchor of three or more names
is a column's and nothing else, and one of two names is a variable's only under a label the routine
itself holds; and ``UESCAPE`` after a ``U&''`` string is the whole keyword, never the start of a
longer name.

Not followed, as the rule states: text computed from pieces,
values arriving as data, a time fixed when an object is installed. The code half of CLK-05 (Python
and the tests' own SQL) is a later part's.
"""

import re
import sys
from unittest.mock import patch

import pgparse
from django.db import connection, transaction
from django.test import TransactionTestCase

from verification.clock import clock_census_v1_2 as census

# The three faces are where the time is read. In production each is its built-in; in an owned
# test database each is its built-in and the test clock's offset.
FACES = {
    "public.intevia_clock_statement()": "statement_timestamp()",
    "public.intevia_clock_instant()": "clock_timestamp()",
    "public.intevia_clock_transaction()": "transaction_timestamp()",
}

# The moving part of the test clock (an owned test database only; production refuses a database
# that holds it). Its routines read real time to guard a move. Named one by one: a new routine
# is not excused until it is added here.
MOVING_PART = (
    "public.intevia_test_clock_advance(bigint, timestamp with time zone, bigint)",
    "public.intevia_test_clock_is_clone(text)",
    "public.intevia_test_clock_observed_obstacle()",
    "public.intevia_test_clock_obstacle(oid, oid, boolean)",
    "public.intevia_test_clock_offset()",
    "public.intevia_test_clock_refuse(text)",
    "public.intevia_test_clock_require_clone()",
    "public.intevia_test_clock_reset()",
    "public.intevia_test_clock_shift()",
)

SYSTEM_SCHEMAS = "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%'"


def _not_extension(catalog, oid):
    """The condition that an object is not a member of an extension (rule v2.1 section 2: non-extension objects only).
    Every source applies it to the object that owns the text: the routine, relation or type."""
    return ("NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend e WHERE e.classid = 'pg_catalog.%s'::pg_catalog.regclass"
            " AND e.objid = %s AND e.deptype = 'e')" % (catalog, oid))


# Everything the catalogue holds that PostgreSQL will evaluate, one row per object: its kind, a
# name a person can find it by, and its texts as PostgreSQL prints them back. Each text is read
# on its own, so nothing in one can reach into the next.
CATALOGUE = (
    ("function", """
        SELECT n.nspname || '.' || p.proname || '(' || pg_catalog.oidvectortypes(p.proargtypes) || ')',
               l.lanname, p.prosrc,
               pg_catalog.pg_get_function_sqlbody(p.oid),
               pg_catalog.pg_get_expr(p.proargdefaults, 0),
               CASE WHEN l.lanname = 'plpgsql' THEN pg_catalog.pg_get_functiondef(p.oid) END,
               p.proconfig, p.prosecdef, pg_catalog.pg_get_userbyid(p.proowner)::text
          FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
          JOIN pg_catalog.pg_language l ON l.oid = p.prolang
         WHERE """ + SYSTEM_SCHEMAS + """ AND p.prokind IN ('f', 'p', 'w')
           AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend e
                            WHERE e.classid = 'pg_catalog.pg_proc'::regclass AND e.objid = p.oid AND e.deptype = 'e')"""),
    ("column default", """
        SELECT n.nspname || '.' || c.relname || '.' || a.attname, pg_catalog.pg_get_expr(d.adbin, d.adrelid)
          FROM pg_catalog.pg_attrdef d
          JOIN pg_catalog.pg_class c ON c.oid = d.adrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.adnum
         WHERE """ + SYSTEM_SCHEMAS + " AND " + _not_extension("pg_class", "c.oid")),
    ("view", """
        SELECT n.nspname || '.' || c.relname, pg_catalog.pg_get_viewdef(c.oid)
          FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE """ + SYSTEM_SCHEMAS + """ AND c.relkind IN ('v', 'm') AND """ + _not_extension("pg_class", "c.oid")),
    # A constraint trigger is also a trigger, and its condition is read with the triggers.
    ("constraint", """
        SELECT n.nspname || '.' || COALESCE(c.relname, t.typname) || ' ' || o.conname, o.contypid <> 0,
               pg_catalog.pg_get_constraintdef(o.oid)
          FROM pg_catalog.pg_constraint o
          JOIN pg_catalog.pg_namespace n ON n.oid = o.connamespace
          LEFT JOIN pg_catalog.pg_class c ON c.oid = o.conrelid
          LEFT JOIN pg_catalog.pg_type t ON t.oid = o.contypid
         WHERE """ + SYSTEM_SCHEMAS + """ AND o.contype <> 't'
           AND """ + _not_extension("pg_class", "o.conrelid") + " AND " + _not_extension("pg_type", "o.contypid")),
    ("trigger", """
        SELECT n.nspname || '.' || c.relname || ' ' || g.tgname, pg_catalog.pg_get_triggerdef(g.oid)
          FROM pg_catalog.pg_trigger g
          JOIN pg_catalog.pg_class c ON c.oid = g.tgrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE """ + SYSTEM_SCHEMAS + """ AND NOT g.tgisinternal AND """ + _not_extension("pg_class", "c.oid")),
    ("index", """
        SELECT n.nspname || '.' || x.relname, pg_catalog.pg_get_indexdef(i.indexrelid)
          FROM pg_catalog.pg_index i
          JOIN pg_catalog.pg_class x ON x.oid = i.indexrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = x.relnamespace
         WHERE """ + SYSTEM_SCHEMAS + " AND " + _not_extension("pg_class", "i.indrelid") + " AND " + _not_extension("pg_class", "i.indexrelid")),
    # The places CK-2R9 adds (acceptance rule v2.1, section 2). INTEVIA holds none of them today; each may be empty.
    ("domain default", """
        SELECT n.nspname || '.' || t.typname, pg_catalog.pg_get_expr(t.typdefaultbin, 0)
          FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
         WHERE """ + SYSTEM_SCHEMAS + """ AND t.typdefaultbin IS NOT NULL AND """ + _not_extension("pg_type", "t.oid")),
    ("policy", """
        SELECT n.nspname || '.' || c.relname || ' ' || p.polname,
               pg_catalog.pg_get_expr(p.polqual, p.polrelid), pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid)
          FROM pg_catalog.pg_policy p
          JOIN pg_catalog.pg_class c ON c.oid = p.polrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE """ + SYSTEM_SCHEMAS + " AND " + _not_extension("pg_class", "c.oid")),
    ("rule", """
        SELECT n.nspname || '.' || c.relname || ' ' || r.rulename, pg_catalog.pg_get_ruledef(r.oid)
          FROM pg_catalog.pg_rewrite r
          JOIN pg_catalog.pg_class c ON c.oid = r.ev_class
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE """ + SYSTEM_SCHEMAS + """ AND r.rulename <> '_RETURN' AND """ + _not_extension("pg_class", "c.oid")),
    ("aggregate initial value", """
        SELECT n.nspname || '.' || p.proname || '(' || pg_catalog.oidvectortypes(p.proargtypes) || ')',
               a.agginitval, a.aggminitval
          FROM pg_catalog.pg_aggregate a
          JOIN pg_catalog.pg_proc p ON p.oid = a.aggfnoid
          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
         WHERE """ + SYSTEM_SCHEMAS + " AND " + _not_extension("pg_proc", "p.oid")),
    ("publication filter", """
        SELECT b.pubname || ' ' || n.nspname || '.' || c.relname, pg_catalog.pg_get_expr(r.prqual, r.prrelid)
          FROM pg_catalog.pg_publication_rel r
          JOIN pg_catalog.pg_publication b ON b.oid = r.prpubid
          JOIN pg_catalog.pg_class c ON c.oid = r.prrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE r.prqual IS NOT NULL AND """ + _not_extension("pg_class", "c.oid") + " AND " + _not_extension("pg_publication", "b.oid")),
    # F6 by type: every column, routine argument or return, and type (a domain, an array) that is timetz or is
    # built on it. Each row is a use.
    ("timetz use", """
        WITH RECURSIVE tz(oid) AS (
            SELECT 'pg_catalog.timetz'::pg_catalog.regtype::oid
            UNION
            SELECT t.oid FROM pg_catalog.pg_type t JOIN tz ON t.typbasetype = tz.oid OR t.typelem = tz.oid)
        SELECT 'column ' || n.nspname || '.' || c.relname || '.' || a.attname
          FROM pg_catalog.pg_attribute a
          JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
         WHERE """ + SYSTEM_SCHEMAS + """ AND a.attnum > 0 AND NOT a.attisdropped AND a.atttypid IN (SELECT oid FROM tz)
           AND """ + _not_extension("pg_class", "c.oid") + """
        UNION ALL
        SELECT 'routine ' || n.nspname || '.' || p.proname || '(' || pg_catalog.oidvectortypes(p.proargtypes) || ')'
          FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
         WHERE """ + SYSTEM_SCHEMAS + """ AND (p.prorettype IN (SELECT oid FROM tz) OR EXISTS (
               SELECT 1 FROM pg_catalog.unnest(COALESCE(p.proallargtypes, p.proargtypes::pg_catalog.oid[])) u(t)
                WHERE u.t IN (SELECT oid FROM tz))) AND """ + _not_extension("pg_proc", "p.oid") + """
        UNION ALL
        SELECT 'type ' || n.nspname || '.' || t.typname
          FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
         WHERE """ + SYSTEM_SCHEMAS + """ AND t.oid IN (SELECT oid FROM tz)
           AND """ + _not_extension("pg_type", "t.oid") + " AND " + _not_extension("pg_type", "t.typelem")),
)
TYPE_USES = ("timetz use",)
REQUIRED_KINDS = ("function", "column default", "view", "constraint", "trigger", "index")

# What counts as a time read in a parse tree (the CK-2R9 acceptance rule v2.1, section 1).
CALLED_BY_NAME = ("clock_timestamp", "timeofday", "statement_timestamp", "transaction_timestamp", "now")
KEYWORDS = {
    "SVFOP_CURRENT_DATE": "current_date",
    "SVFOP_CURRENT_TIME": "current_time",
    "SVFOP_CURRENT_TIME_N": "current_time",
    "SVFOP_CURRENT_TIMESTAMP": "current_timestamp",
    "SVFOP_CURRENT_TIMESTAMP_N": "current_timestamp",
    "SVFOP_LOCALTIME": "localtime",
    "SVFOP_LOCALTIME_N": "localtime",
    "SVFOP_LOCALTIMESTAMP": "localtimestamp",
    "SVFOP_LOCALTIMESTAMP_N": "localtimestamp",
}
# F4: a clock word, in any letter case, with no letter directly before or after it. Digits, "_", punctuation, spaces
# and the string's ends are boundaries ('tomorrow10:00', '_today_', 'due:today'); 'known' and 'nowhere' hold none.
# The word and its boundaries are ASCII only (rule v2.1 section 1, a-z): with re.ASCII, letter case is folded for
# ASCII letters alone, so U+212A KELVIN SIGN is a boundary ('\u212atoday' holds one) and U+017F LONG S is not an s.
CLOCK_WORD = re.compile(r"(?<![A-Za-z])(?:now|today|tomorrow|yesterday)(?![A-Za-z])", re.IGNORECASE | re.ASCII)
TIME_TYPES = ("timestamp", "timestamptz", "date", "time", "timetz")
# F6: timetz and its array type, by their names in pg_catalog.
TIMETZ = ("timetz", "_timetz")

# libpg_query reads PL/pgSQL without the catalogue, so it does not know the fields of a variable
# declared with a table's row type, and refuses an assignment to one ("v.x" is not a known
# variable). PostgreSQL itself reads such a variable as a record. When libpg_query names such a
# variable, a declaration of it is read as ``record`` and the definition is read again, but only
# where the declared type is itself a composite type (a table's, a view's or a declared one), as the
# catalogue resolves that very name under the routine's own search path (``x%ROWTYPE``: the row type
# of the relation the catalogue finds as ``x``, if it has one; an index has none): an integer, a domain, a
# name the catalogue does not resolve, or a quoted name has no fields here, and the routine stays
# unreadable. Each place the text has the variable's name, such a type and a semicolon is tried in
# turn, each from the same text (a comment or a string may hold one before the declaration, and a
# try that does not satisfy the parser is undone), until libpg_query reads the routine or names
# another variable (the try is kept and that variable is taken next) or no place is left, when the
# routine is reported unreadable. A try replaces one type name with ``record``, never a time type.
_UNKNOWN_FIELD = re.compile(r'^"([A-Za-z_][A-Za-z0-9_]*)\.[^"]*" is not a known variable')
_DECLARED_TYPE = r"(?<![\w.$\"]){variable}\s+((?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*(?:%ROWTYPE)?)\s*;"
NEVER_REPLACED = set(TIME_TYPES) | {"current_date", "current_time", "current_timestamp", "localtime", "localtimestamp"}
TABLE_ROW_TYPES = frozenset({"public.core_cqact", "core_cqact"})


def _type_names(written, arrays=False):
    """The qualified name PostgreSQL's parser reads in ``NULL::<written>``, else None; an array type's element name
    only when ``arrays`` is set."""
    try:
        tree = pgparse.parse("SELECT NULL::" + (written or "").strip())
    except pgparse.PGQueryError:
        return None
    targets = tree[0]["stmt"].get("SelectStmt", {}).get("targetList", []) if len(tree) == 1 else []
    value = targets[0].get("ResTarget", {}).get("val", {}) if len(targets) == 1 else {}
    cast = value.get("TypeCast", {})
    if cast.get("arg", {}).get("A_Const", {}).get("isnull") is not True:
        return None
    if cast.get("typeName", {}).get("arrayBounds") and not arrays:
        return None
    return _names(cast.get("typeName", {}).get("names", [])) or None


def _search_path_of(settings):
    """A routine's own search path, from its stored settings (``proconfig``), as a list of schema names; else None."""
    for entry in settings or []:
        name, _, value = entry.partition("=")
        if name == "search_path":
            if value.strip() in ('""', "''", ""):
                return []   # an empty path: only the implicit schemas
            try:
                statement = pgparse.parse("SET search_path TO " + value)[0]["stmt"]["VariableSetStmt"]
            except (pgparse.PGQueryError, IndexError, KeyError):
                return None
            return [_string_constant(arg) or "" for arg in statement.get("args", [])]
    return None


class TableTypes:
    """Types for the reading's own tables, which have no catalogue: a name means PostgreSQL's own type of that
    name, and the row types are the ones listed."""

    def __init__(self, row_types=frozenset()):
        self.row_types = row_types

    def is_row_type(self, written, search_path=None):
        name = re.sub(r"%rowtype$", "", written.lower())
        return name in self.row_types and name not in NEVER_REPLACED

    def is_timetz(self, names, search_path=None):
        return bool(names) and names[-1] in TIMETZ and names[:-1] in ([], ["pg_catalog"])

    def column_is_timetz(self, names, search_path=None):
        return None   # no catalogue: a column's type is not known

    def within(self, search_path):
        return self


class CatalogueTypes:
    """Types as the catalogue resolves them. A name is looked up as PostgreSQL looks it up: qualified, in its schema;
    unqualified, in each schema of the search path in turn (the routine's own ``SET search_path``, with ``pg_temp`` and
    ``pg_catalog`` first unless it names them; without one, the reading session's effective path). The session's
    search path is never changed. A type is followed through any domains to its base type; a name that cannot be
    resolved is not known. ``x%ROWTYPE`` names a relation, which has a row type only if the catalogue gives it one.

    A limit, stated (owed ledger C1-130, item 11): ``CHAIN`` follows a domain to its base type and does not follow an
    array type to its element. The array type of a domain over timetz (``moment[]``) is therefore not resolved to
    timetz here, in a cast or through ``%TYPE``. The catalogue's "timetz use" place does follow ``typelem``, so a
    column, routine argument or return, or type built on such an array is still reported there."""

    CHAIN = """
        WITH RECURSIVE chain AS (
            SELECT t.oid, t.typtype, t.typbasetype, 0 AS depth FROM pg_catalog.pg_type t WHERE t.oid = %s
            UNION ALL
            SELECT b.oid, b.typtype, b.typbasetype, c.depth + 1
              FROM pg_catalog.pg_type b JOIN chain c ON b.oid = c.typbasetype WHERE c.typtype = 'd')
        SELECT (SELECT typtype FROM chain WHERE depth = 0), n.nspname, t.typname
          FROM chain c
          JOIN pg_catalog.pg_type t ON t.oid = c.oid
          JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
         ORDER BY c.depth DESC LIMIT 1"""

    def __init__(self, cursor):
        self.cursor, self.seen = cursor, {}

    def _one(self, sql, params):
        self.cursor.execute(sql, params)
        row = self.cursor.fetchone()
        return row if row is None or len(row) > 1 else row[0]

    def session_path(self):
        """The reading session's search path as set (``$user`` not yet expanded), as a list of schema names."""
        return _search_path_of(["search_path=" + self._one("SELECT pg_catalog.current_setting('search_path')", [])]) or []

    def _schemas(self, search_path):
        if search_path is None:
            return list(self._one("SELECT pg_catalog.current_schemas(true)", []))
        user = self._one("SELECT CURRENT_USER::text", [])
        schemas = [user if schema == "$user" else schema for schema in search_path]
        for implicit in ("pg_catalog", "pg_temp"):
            if implicit not in schemas:
                schemas.insert(0, implicit)
        return schemas

    def _find(self, what, names, search_path):
        """The OID of the type or relation a parsed name designates under a search path, or None."""
        key = (what, tuple(names), tuple(search_path) if search_path is not None else None)
        if key not in self.seen:
            lookup = "pg_catalog.to_regtype" if what == "type" else "pg_catalog.to_regclass"
            places = [names] if len(names) > 1 else [[schema, names[0]] for schema in self._schemas(search_path)]
            found = None
            for qualified in places:
                found = self._one("SELECT %s(%%s)::oid" % lookup, [_quoted(qualified)])
                if found:
                    break
            self.seen[key] = found or None
        return self.seen[key]

    def _chain(self, oid):
        return self._one(self.CHAIN, [oid]) if oid else None

    def is_timetz(self, names, search_path=None):
        """Say whether a parsed type name resolves, through any domains, to timetz (or its array) in pg_catalog."""
        found = self._chain(self._find("type", names, search_path)) if names else None
        return bool(found) and found[1] == "pg_catalog" and found[2] in TIMETZ

    def column_is_timetz(self, names, search_path=None):
        """Say whether ``[schema.]relation.column`` names a column whose type resolves, through any domains, to timetz
        (or its array); None when the catalogue has no such column."""
        if len(names) < 2:
            return None
        relation = self._find("relation", names[:-1], search_path)
        if not relation:
            return None
        column = self._one("SELECT atttypid FROM pg_catalog.pg_attribute WHERE attrelid = %s AND attname = %s "
                           "AND attnum > 0 AND NOT attisdropped", [relation, names[-1]])
        if not column:
            return None
        found = self._chain(column)
        return bool(found) and found[1] == "pg_catalog" and found[2] in TIMETZ

    def is_row_type(self, written, search_path=None):
        relation = re.match(r"(.*)%rowtype$", written, flags=re.I)
        names = _type_names(relation.group(1) if relation else written)
        if not names:
            return False
        if relation:
            found = self._find("relation", names, search_path)
            row_type = self._one("SELECT reltype FROM pg_catalog.pg_class WHERE oid = %s", [found]) if found else None
        else:
            row_type = self._find("type", names, search_path)
        chain = self._chain(row_type)
        return bool(chain) and chain[0] == "c"

    def within(self, search_path):
        return RoutineTypes(self, search_path)


class RoutineTypes:
    """The catalogue's types for one routine, under its own search path. A routine read from its catalogue row keeps
    the path the row gives (``bound``), with a SECURITY DEFINER routine's ``$user`` already its owner; its printed
    header does not replace it."""

    def __init__(self, catalogue, search_path, bound=False):
        self.catalogue, self.search_path, self.bound = catalogue, search_path, bound

    def is_timetz(self, names, search_path=None):
        return self.catalogue.is_timetz(names, self.search_path)

    def is_row_type(self, written, search_path=None):
        return self.catalogue.is_row_type(written, self.search_path)

    def column_is_timetz(self, names, search_path=None):
        return self.catalogue.column_is_timetz(names, self.search_path)

    def within(self, search_path):
        return self if self.bound or search_path == self.search_path else RoutineTypes(self.catalogue, search_path)


def _names(nodes):
    return [part["String"]["sval"] for part in nodes if "String" in part]


def _string_constant(node):
    const = node.get("A_Const") if isinstance(node, dict) else None
    return const["sval"]["sval"] if const and "sval" in const else None


def _quoted(names):
    return ".".join('"%s"' % name.replace('"', '""') for name in names)


def string_readings(text):
    """The reads written in one string constant's value: a census pattern (F3) and a clock word (F4)."""
    found = set()
    for pattern, _meaning in census.DB_CLOCKS:
        for match in pattern.finditer(text):
            found.add("in a string: " + re.sub(r"\s+", "", match.group(0)).lower())
    if CLOCK_WORD.search(text):
        found.add("clock word in a string: '%s'" % text)
    return found


def reads_in_tree(tree, types=None):
    """Return the time reads written in one parse tree (as libpg_query gives it), each as a short name: a clock
    function called by name (F1), a time keyword (F2), a string constant's census pattern or clock word (F3, F4),
    ``age`` with one argument (F5) and any use of the type timetz (F6): a cast to it in either form, a type named in
    a statement, or a PL/pgSQL variable declared with it. No value is followed and nothing is excused: what is
    written is reported. ``types`` resolves a type's name (by default PostgreSQL's own type of that name; reading the
    catalogue, the type the catalogue resolves, through its domains)."""
    found = set()
    types = types or TableTypes()
    declared, labels = _declarations(tree), _labels(tree)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "FuncCall":
                    name = _names(value.get("funcname", []))
                    builtin = bool(name) and name[:-1] in ([], ["pg_catalog"])
                    if builtin and name[-1] in CALLED_BY_NAME:
                        found.add(name[-1] + "()")
                    if builtin and name[-1] == "age" and len(value.get("args", [])) == 1:
                        found.add("age() with one argument")
                    if len(value.get("args", [])) == 1 and types.is_timetz(name):
                        found.add("timetz")   # the function-style form of a cast to timetz
                elif key == "SQLValueFunction" and value.get("op") in KEYWORDS:
                    found.add(KEYWORDS[value["op"]])
                elif key == "typeName" and isinstance(value, dict) and types.is_timetz(_names(value.get("names", []))):
                    found.add("timetz")   # a cast, a column or an argument of type timetz
                elif key == "PLpgSQL_type" and _declared_timetz(value.get("typname"), declared, labels, types, found):
                    found.add("timetz")   # a PL/pgSQL variable declared with it, by name or through %TYPE
                elif key == "A_Const" and "sval" in value:
                    found.update(string_readings(value["sval"]["sval"]))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tree)
    return found


# Every string constant written in a text, found by reading the text as PostgreSQL's lexer does (rule v2.1 section
# 1: F3 and F4 apply to every string constant, as decoded, in any quoting). The parse tree holds most of them as
# constants, but the grammar keeps some as plain fields (a RAISE message, a NOTIFY payload, a COMMENT, an option's
# value, an enum label, a nested routine's body), so the reading does not depend on the tree's shape: it reads the
# literals from the text itself, and every constant the parse tree holds at a literal's place must agree with it
# (``_literals_agree``), or the text is reported unreadable. Plain strings are read with standard_conforming_strings
# on, PostgreSQL's default (rule v2.1 section 5). Bit strings (B'', X'') are not text and are not read.
_SPACE = " \t\n\r\f\v"
_IDENT_START = re.compile(r"[A-Za-z_\u0080-\U0010ffff]")
_IDENT_REST = re.compile(r"[A-Za-z0-9_$\u0080-\U0010ffff]*")
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_\u0080-\U0010ffff][A-Za-z0-9_\u0080-\U0010ffff]*)?\$")


# UESCAPE after a U&'' string is the keyword alone: a longer name that starts with it (uescape_alias) is a name.
_UESCAPE = re.compile(r"uescape(?![A-Za-z0-9_$\u0080-\U0010ffff])", re.IGNORECASE)


class LexerRefused(ValueError):
    """A text the string-literal reading cannot finish (an unterminated quote or comment): reported unreadable."""


def _surrogates(codes):
    out, i = [], 0
    while i < len(codes):
        c = codes[i]
        if 0xD800 <= c <= 0xDBFF and i + 1 < len(codes) and 0xDC00 <= codes[i + 1] <= 0xDFFF:
            out.append(0x10000 + ((c - 0xD800) << 10) + (codes[i + 1] - 0xDC00)); i += 2
        else:
            out.append(c); i += 1
    return "".join(chr(c) for c in out)


def _decode_escape_string(raw):
    """An E'' string's body (quotes already undoubled except backslash forms), decoded as PostgreSQL decodes it."""
    codes, i = [], 0
    simple = {"b": 8, "f": 12, "n": 10, "r": 13, "t": 9}
    while i < len(raw):
        ch = raw[i]
        if ch != "\\" or i + 1 == len(raw):
            codes.append(ord(ch)); i += 1; continue
        nxt = raw[i + 1]
        if nxt in simple:
            codes.append(simple[nxt]); i += 2
        elif nxt in "01234567":
            m = re.match(r"[0-7]{1,3}", raw[i + 1:]); codes.append(int(m.group(0), 8) & 0xFF); i += 1 + len(m.group(0))
        elif nxt == "x" and re.match(r"[0-9A-Fa-f]", raw[i + 2:i + 3]):
            m = re.match(r"[0-9A-Fa-f]{1,2}", raw[i + 2:]); codes.append(int(m.group(0), 16)); i += 2 + len(m.group(0))
        elif nxt == "u" and re.fullmatch(r"[0-9A-Fa-f]{4}", raw[i + 2:i + 6]):
            codes.append(int(raw[i + 2:i + 6], 16)); i += 6
        elif nxt == "U" and re.fullmatch(r"[0-9A-Fa-f]{8}", raw[i + 2:i + 10]):
            codes.append(int(raw[i + 2:i + 10], 16)); i += 10
        else:
            codes.append(ord(nxt)); i += 2
    return _surrogates(codes)


def _decode_unicode_string(raw, escape):
    """A U&'' string's body decoded with its escape character."""
    codes, i = [], 0
    while i < len(raw):
        if raw[i] == escape:
            if raw[i + 1:i + 2] == escape:
                codes.append(ord(escape)); i += 2
            elif raw[i + 1:i + 2] == "+" and re.fullmatch(r"[0-9A-Fa-f]{6}", raw[i + 2:i + 8]):
                codes.append(int(raw[i + 2:i + 8], 16)); i += 8
            elif re.fullmatch(r"[0-9A-Fa-f]{4}", raw[i + 1:i + 5]):
                codes.append(int(raw[i + 1:i + 5], 16)); i += 5
            else:
                raise LexerRefused("invalid Unicode escape")
        else:
            codes.append(ord(raw[i])); i += 1
    return _surrogates(codes)


_LINE_END = re.compile(r"[\n\r]")


def _comment_end(text, i):
    """Where the "--" comment that starts at i ends: at the first newline or carriage return after it, as PostgreSQL
    ends it (scan.l: comment is "--" then non_newline*, and non_newline is [^\\n\\r]); -1 where the text ends first."""
    end = _LINE_END.search(text, i)
    return end.start() if end else -1


def _skip_space_and_comments(text, i, comments_end_line_only=False):
    """Skip whitespace and comments from i; return (new i, whether a newline was crossed)."""
    newline = False
    while i < len(text):
        if text[i] in _SPACE:
            newline |= text[i] in "\n\r"; i += 1
        elif text.startswith("--", i):
            end = _comment_end(text, i)
            if end < 0:
                return len(text), newline
            i = end
        elif text.startswith("/*", i) and not comments_end_line_only:
            depth, i = 1, i + 2
            while depth:
                if i >= len(text):
                    raise LexerRefused("unterminated comment")
                if text.startswith("/*", i):
                    depth += 1; i += 2
                elif text.startswith("*/", i):
                    depth -= 1; i += 2
                else:
                    i += 1
        else:
            break
    return i, newline


def _continuation(text, i):
    """Where a quoted string may continue after i: horizontal whitespace or "--" comments, a newline, then any
    whitespace and comments that each end at a newline (scan.l: whitespace_with_newline, horiz_whitespace holding
    comments). None when no newline follows."""
    while i < len(text) and (text[i] in " \t\f" or text.startswith("--", i)):
        if text.startswith("--", i):
            end = _comment_end(text, i)
            if end < 0:
                return None
            i = end
            break
        i += 1
    if i >= len(text) or text[i] not in "\n\r":
        return None
    while i < len(text):
        if text[i] in _SPACE:
            i += 1
        elif text.startswith("--", i):
            end = _comment_end(text, i)
            if end < 0:
                return None
            i = end
        else:
            break
    return i


def _quoted_body(text, i, escapes):
    """Read one quoted segment starting at the quote at i; return (raw body, index after the closing quote)."""
    out, i = [], i + 1
    while True:
        if i >= len(text):
            raise LexerRefused("unterminated quoted string")
        ch = text[i]
        if escapes and ch == "\\" and i + 1 < len(text):
            out.append(text[i:i + 2]); i += 2
        elif ch == "'":
            if text.startswith("''", i):
                out.append("\\'" if escapes else "'"); i += 2
            else:
                return "".join(out), i + 1
        else:
            out.append(ch); i += 1


def _string_literals(text):
    """Return every string constant written in ``text`` as (offset, kind, decoded value), in order."""
    found, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in _SPACE or text.startswith("--", i) or text.startswith("/*", i):
            i, _ = _skip_space_and_comments(text, i)
            continue
        if ch == '"':
            end = i + 1
            while True:
                end = text.find('"', end)
                if end < 0:
                    raise LexerRefused("unterminated quoted name")
                if text.startswith('""', end):
                    end += 2; continue
                break
            i = end + 1
            continue
        start, kind = i, None
        if ch == "'":
            kind = "plain"
        elif ch in "EeNn" and text[i + 1:i + 2] == "'":
            kind, i = ("escape" if ch in "Ee" else "plain"), i + 1
        elif ch in "BbXx" and text[i + 1:i + 2] == "'":
            _, i = _quoted_body(text, i + 1, False)   # a bit string: not text
            continue
        elif ch in "Uu" and text[i + 1:i + 2] == "&" and text[i + 2:i + 3] in ("'", '"'):
            if text[i + 2] == '"':
                i += 2
                continue   # a Unicode-escaped name: read as a quoted name next
            kind, i = "unicode", i + 2
        elif ch == "$":
            tag = _DOLLAR_TAG.match(text, i)
            if tag:
                end = text.find(tag.group(0), tag.end())
                if end < 0:
                    raise LexerRefused("unterminated dollar quote")
                found.append((start, "dollar", text[tag.end():end]))
                i = end + len(tag.group(0))
                continue
            i += 1   # a parameter ($1) or an operator character
            while i < n and text[i].isdigit():
                i += 1
            continue
        elif _IDENT_START.match(ch):
            i = _IDENT_REST.match(text, i + 1).end()
            continue
        elif ch.isdigit() or (ch == "." and text[i + 1:i + 2].isdigit()):
            m = re.compile(r"[0-9_]*\.?[0-9_]*(?:[eE][+-]?[0-9_]+)?|0[xXoObB][0-9A-Fa-f_]+").match(text, i)
            i = max(m.end(), i + 1)
            continue
        else:
            i += 1
            continue
        raw, i = _quoted_body(text, i, kind == "escape")
        parts = [raw]
        while True:   # a string continued after whitespace that holds a newline (scan.l: quotecontinue)
            j = _continuation(text, i)
            if j is not None and text[j:j + 1] == "'":
                more, i = _quoted_body(text, j, kind == "escape")
                parts.append(more)
            else:
                break
        raw = "".join(parts)
        if kind == "escape":
            value = _decode_escape_string(raw)
        elif kind == "unicode":
            escape = "\\"
            j, _ = _skip_space_and_comments(text, i)
            word = _UESCAPE.match(text, j)
            if word:
                k, _ = _skip_space_and_comments(text, word.end())
                if text[k:k + 1] != "'" or text[k + 2:k + 3] != "'":
                    raise LexerRefused("invalid UESCAPE")
                escape, i = text[k + 1], k + 3
            value = _decode_unicode_string(raw, escape)
        else:
            value = raw
        found.append((start, kind, value))
    return found


def _constants(tree, prefix):
    """Every string constant a parse tree holds with its place in the text (its location less the prefix's length)."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            constant = node.get("A_Const")
            if isinstance(constant, dict) and "sval" in constant and "location" in constant:
                found.append((constant["location"] - prefix, constant["sval"]["sval"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tree)
    return found


def _literals_agree(text, tree, prefix):
    """Say where a parse tree's constant at a string literal's place differs from the literal read from the text, or
    where the tree holds a constant at a place that begins a quote the reading did not find."""
    # The parser gives places in bytes of UTF-8; the reading counts characters.
    literals = {len(text[:offset].encode("utf-8")): value for offset, _, value in _string_literals(text)}
    raw = text.encode("utf-8")
    for offset, value in _constants(tree, prefix):
        if offset in literals:
            if literals[offset] != value:
                return "the string constant at byte %d reads differently" % offset
        elif offset >= 0 and re.match(rb"(?i)(?:[EN]|U&)?'|\$", raw[offset:offset + 3]):
            return "a string constant at byte %d was not found in the text" % offset
    return None


def _declarations(tree):
    """A PL/pgSQL routine's variables, by name, with the type each is declared with as written (its parameters too,
    also as $1, $2, ...); empty for any other tree."""
    declared, parameters = {}, []
    for function in (tree if isinstance(tree, list) else [tree]):
        datums = function.get("PLpgSQL_function", {}).get("datums", []) if isinstance(function, dict) else []
        for datum in datums:
            variable = datum.get("PLpgSQL_var") or datum.get("PLpgSQL_rec") or {}
            name = variable.get("refname")
            written = variable.get("datatype", {}).get("PLpgSQL_type", {}).get("typname")
            if name == "found" and "lineno" not in variable:
                parameters = list(declared)   # PL/pgSQL declares its parameters first, then FOUND
            if name and name not in declared:
                declared[name] = written
        for number, name in enumerate(parameters, 1):
            declared.setdefault("$%d" % number, declared[name])
    return declared


def _labels(tree):
    """The block and loop labels a PL/pgSQL routine's statements carry (``<<label>>``); empty for any other tree. The
    routine's own name, which PL/pgSQL also accepts as the label of its parameters, is not in the tree and is not
    given here: an anchor written ``routine.parameter%TYPE`` is reported unreadable (fail closed)."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("PLpgSQL_stmt_") and isinstance(value, dict) and isinstance(value.get("label"), str):
                    found.add(value["label"])
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tree)
    return found


def _anchor_names(written):
    """The parsed names of a %TYPE anchor (``a.b.c`` of ``a.b.c%TYPE``), else None."""
    try:
        tree = pgparse.parse("SELECT " + written)
        value = tree[0]["stmt"]["SelectStmt"]["targetList"][0]["ResTarget"]["val"]
    except (pgparse.PGQueryError, IndexError, KeyError):
        return None
    if "ParamRef" in value:
        return ["$%d" % value["ParamRef"].get("number", 0)]
    fields = value.get("ColumnRef", {}).get("fields", [])
    names = [field.get("String", {}).get("sval") for field in fields]
    return names if names and all(names) else None


def _declared_timetz(written, declared, labels, types, found, depth=0):
    """Say whether a PL/pgSQL variable's written type is timetz: a type name resolved as any type name is, or an
    ``x%TYPE`` anchor followed to what it names as PL/pgSQL does (PostgreSQL 17.11, VC run): a variable or parameter
    (``v``, ``$1``, ``label.v``) or a column (``table.column``, ``schema.table.column``) (rule v2.1 section 1, F6: a
    variable declared with it). PL/pgSQL reads ``label.v`` only where the anchor has exactly two names (pl_comp.c,
    plpgsql_parse_cwordtype); with three or more it names a column and nothing else. So a variable is followed only
    for one name, or for two where the first is a label the routine holds (``_labels``). With the catalogue, an
    anchor it cannot resolve is reported unreadable, and never read as a variable that shares its last name."""
    written = (written or "").strip()
    anchor = re.match(r"(.*)%type$", written, flags=re.I)
    if not anchor:
        return types.is_timetz(_type_names(written, arrays=True))
    names = _anchor_names(anchor.group(1))
    if names and len(names) == 1 and names[0] in declared and depth < 10:
        return _declared_timetz(declared[names[0]], declared, labels, types, found, depth + 1)
    if names and len(names) >= 2:
        column = types.column_is_timetz(names)
        if column is not None:
            return column
        if len(names) == 2 and names[0] in labels and names[1] in declared and depth < 10:   # a label's variable
            return _declared_timetz(declared[names[1]], declared, labels, types, found, depth + 1)
    if not isinstance(types, TableTypes):
        found.add("unreadable: %%TYPE anchor not resolved: %s" % written)
    return False


def _routine_header(definition):
    """Read a PL/pgSQL routine's own search path from its header (or None)."""
    try:
        statement = pgparse.parse(definition)[0]["stmt"].get("CreateFunctionStmt", {})
    except (pgparse.PGQueryError, IndexError, KeyError):
        return None
    path = None
    for option in statement.get("options", []):
        element = option.get("DefElem", {})
        setting = element.get("arg", {}).get("VariableSetStmt", {}) if element.get("defname") == "set" else {}
        if setting.get("name") == "search_path":
            path = [_string_constant(arg) or "" for arg in setting.get("args", [])]
    return path


def _plpgsql_expressions(tree):
    """Return every SQL text inside a PL/pgSQL routine as (parse mode, text)."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "PLpgSQL_expr":
                    found.append((value.get("parseMode", 0), value["query"]))
                    continue
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tree)
    return found


OPERATOR_PROBE = "clk05_operator_stands_here"


def _assignment(query):
    """Return the parse trees of a PL/pgSQL assignment's target and value. Each ``:=`` or ``=`` in turn is tried as
    the operator, and the first place is taken where the operator stands in code (PostgreSQL's lexer, reading the
    text before it with a probe name added, still sees the probe: the place is not inside a comment, a string or a
    quoted name) and both the text before it and the text after it read as expressions."""
    for match in re.finditer(r":=|=", query):
        try:
            probe = pgparse.parse("SELECT " + query[:match.start()] + " AS " + OPERATOR_PROBE)
            target = pgparse.parse("SELECT " + query[:match.start()])
            value = pgparse.parse("SELECT " + query[match.end():])
        except pgparse.PGQueryError:
            continue
        listed = probe[0]["stmt"].get("SelectStmt", {}).get("targetList", []) if len(probe) == 1 else []
        if not listed or listed[-1].get("ResTarget", {}).get("name") != OPERATOR_PROBE:
            continue  # the place is inside a comment: the probe was read as comment, not as a name
        return target, value
    raise ValueError("the assignment cannot be read: %r" % query)


def _parse_with_row_types(definition, types, path):
    """Read a PL/pgSQL definition, reading as ``record`` a declaration of a row type whose field libpg_query cannot
    see (see above). Each try starts from the text as it stood when that variable was named."""
    try:
        return pgparse.parse_pgsql(definition)
    except pgparse.PGQueryError as error:
        unknown = _UNKNOWN_FIELD.match(str(error))
        if not unknown:
            raise
        variable = unknown.group(1)
        for declared in re.finditer(_DECLARED_TYPE.format(variable=re.escape(variable)), definition):
            if not types.is_row_type(declared.group(1), path):
                continue  # not itself a composite type, as the catalogue resolves it: no fields to assign
            attempt = definition[:declared.start(1)] + "record" + definition[declared.end(1):]
            try:
                return pgparse.parse_pgsql(attempt)
            except pgparse.PGQueryError as again:
                still = _UNKNOWN_FIELD.match(str(again))
                if still and still.group(1) != variable:
                    return _parse_with_row_types(attempt, types, path)  # this variable is read; the next is named
        raise error


def _read_plpgsql(definition, types):
    """Return the parse trees of a PL/pgSQL routine's definition and of every SQL text inside it, each with the types
    its names resolve under (the routine's own search path)."""
    path = _routine_header(definition)
    types = types.within(path)   # every name in the routine resolves under its own search path
    trees = [(_parse_with_row_types(definition, types, path), types)]
    for mode, query in _plpgsql_expressions(trees[0][0]):
        if mode == 0:  # a whole SQL statement
            trees.append((pgparse.parse(query), types))
        elif mode == 2:  # an expression
            trees.append((pgparse.parse("SELECT " + query), types))
        else:  # an assignment: its target, and its value
            target, value = _assignment(query)
            trees += [(target, types), (value, types)]
    return trees


# Each SQL form is read as one statement: the text after a fixed prefix.
PREFIXES = {
    "sql": "",
    "expressions": "SELECT ",
    "sql body": "CREATE FUNCTION pg_temp.clk05() RETURNS void LANGUAGE sql ",
    "table constraint": "ALTER TABLE pg_temp.clk05 ADD CONSTRAINT clk05 ",
    "domain constraint": "ALTER DOMAIN pg_temp.clk05 ADD CONSTRAINT clk05 ",
}
FORMS = {form: (lambda prefix: lambda text, types: [(pgparse.parse(prefix + text), types)])(prefix)
         for form, prefix in PREFIXES.items()}
FORMS["plpgsql"] = _read_plpgsql


def _raise_messages(tree):
    found = []

    def walk(node):
        if isinstance(node, dict):
            raised = node.get("PLpgSQL_stmt_raise")
            if isinstance(raised, dict) and raised.get("message") is not None:
                found.append(raised["message"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tree)
    return found


def written_strings(form, text, trees):
    """Return every string constant written in a text read in the given form, and where the parse trees disagree
    with that reading (else None). A PL/pgSQL routine's body is itself read as text: its literals are the routine's
    strings, and the body as a whole is not one."""
    literals = _string_literals(text)
    if form != "plpgsql":
        return [value for _, _, value in literals], _literals_agree(text, trees[0][0], len(PREFIXES[form]))
    quoted = [literal for literal in literals if literal[1] == "dollar"] or literals
    if not quoted:
        raise LexerRefused("the routine has no quoted body")
    body = quoted[-1]
    inside = _string_literals(body[2])
    strings = [value for literal in literals if literal is not body for _, _, value in [literal]]
    strings += [value for _, _, value in inside]
    disagreement = _literals_agree(text, pgparse.parse(text), 0)
    for mode, query in _plpgsql_expressions(trees[0][0]):
        if disagreement is None and mode in (0, 2):
            prefix = "" if mode == 0 else "SELECT "
            disagreement = _literals_agree(query, pgparse.parse(prefix + query), len(prefix))
    values = {value for _, _, value in inside}
    for message in _raise_messages(trees[0][0]):
        if disagreement is None and message not in values:
            disagreement = "a RAISE message was not found as a string constant in the body"
    return strings, disagreement


def time_readings(form, text, types=None):
    """Return the time reads in one text read in the given form. A text that cannot be read is reported. ``types``
    resolves type names: the catalogue's when the catalogue is read; by default PostgreSQL's own names and no row
    types."""
    try:
        trees = FORMS[form](text, types or TableTypes())
        strings, disagreement = written_strings(form, text, trees)
    except Exception as error:  # noqa: BLE001 - a text that cannot be read is reported, never passed over
        message = str(error).splitlines()[0] if str(error) else type(error).__name__
        return ["unreadable as %s: %s" % (form, message)]
    found = {found for tree, tree_types in trees for found in reads_in_tree(tree, tree_types)}
    for value in strings:
        found |= string_readings(value)
    if disagreement:
        found.add("unreadable as %s: %s" % (form, disagreement))
    return sorted(found)


def routine_texts(language, source, sql_body, defaults, definition):
    """Return a routine's texts as (form, text), each to be read on its own."""
    texts = []
    if sql_body is not None:
        texts.append(("sql body", sql_body))
    elif language == "sql":
        texts.append(("sql", source))
    elif language == "plpgsql":
        texts.append(("plpgsql", definition))
    else:
        texts.append(("a routine in " + language, source))  # no reading for this language: reported
    if defaults is not None:
        texts.append(("expressions", defaults))
    return texts


def function_readings(row, types):
    """Read one function from the catalogue: its texts, with its names resolved under its own search path. ``row``:
    name, language, body, SQL-standard body, argument defaults, definition, settings, whether it is SECURITY
    DEFINER, its owner (as ``CATALOGUE`` gives them). A SECURITY DEFINER routine
    runs as its owner, so ``$user`` in its path (its own, or else the reading session's, which it inherits) names the
    owner; any other routine's ``$user`` is the reading role's."""
    routine = types
    if isinstance(types, CatalogueTypes):
        path = _search_path_of(row[6])
        if row[7]:
            path = [row[8] if schema == "$user" else schema for schema in (path if path is not None else types.session_path())]
        routine = RoutineTypes(types, path, bound=True)
    found = set()
    for form, text in routine_texts(*row[1:6]):
        # A SQL-standard body and argument defaults are stored bound, and printed for the reading session's own path;
        # a body stored as text is read when it runs, under the routine's own path.
        found.update(time_readings(form, text, types if form in ("sql body", "expressions") else routine))
    return sorted(found)


def readings_of(texts, types=None):
    """Return an object's time reads from its texts, each read on its own. A text given as ("string", value) is a
    string constant stored outside any SQL text (an aggregate's initial value), read as F3 and F4 read one."""
    found = set()
    for form, text in texts:
        if text is None:
            continue
        found.update(string_readings(text) if form == "string" else time_readings(form, text, types))
    return sorted(found)


# How each kind's row is read: the forms of its texts, from the row as its query gives it.
ROW_TEXTS = {
    "column default": lambda row: [("expressions", row[1])],
    "view": lambda row: [("sql", row[1])],
    "constraint": lambda row: [("domain constraint" if row[1] else "table constraint", row[2])],
    "trigger": lambda row: [("sql", row[1])],
    "index": lambda row: [("sql", row[1])],
    "domain default": lambda row: [("expressions", row[1])],
    "policy": lambda row: [("expressions", row[1]), ("expressions", row[2])],
    "rule": lambda row: [("sql", row[1])],
    "aggregate initial value": lambda row: [("string", row[1]), ("string", row[2])],
    "publication filter": lambda row: [("expressions", row[1])],
}


def catalogue_readings(cursor, kinds=None):
    """Return how many objects of each kind the catalogue holds, and every time read found, by (kind, name). A kind
    in ``TYPE_USES`` is itself the finding: each row it returns is a use of timetz (F6). ``kinds`` limits the reading
    to the kinds named."""
    seen, readings = {}, {}
    types = CatalogueTypes(cursor)
    for kind, query in CATALOGUE:
        if kinds is not None and kind not in kinds:
            continue
        cursor.execute(query)
        rows = cursor.fetchall()
        seen[kind] = len(rows)
        for row in rows:
            if kind == "function":
                found = function_readings(row, types)
            elif kind in TYPE_USES:
                found = ["timetz"]
            else:
                found = readings_of(ROW_TEXTS[kind](row), types)
            if found:
                readings[(kind, row[0])] = found
    return seen, readings


def plpgsql(body, returns="void", settings=""):
    """A PL/pgSQL routine's definition around a body, as pg_get_functiondef prints one."""
    return ("CREATE OR REPLACE FUNCTION public.clk05_case()\n RETURNS %s\n LANGUAGE plpgsql\n%sAS $function$%s$function$\n"
            % (returns, settings, body))


# What the reading must find, and must pass over. Each line: the form, the text, and the reads in it. The reads are
# written here by hand or by the rule beside each table; none is worked out by the reader.
SPELLINGS = (
    ("sql", "SELECT now(), clock_timestamp(), statement_timestamp(), transaction_timestamp(), timeofday()",
     ["clock_timestamp()", "now()", "statement_timestamp()", "timeofday()", "transaction_timestamp()"]),
    ("sql", "SELECT current_timestamp, current_timestamp(3), localtimestamp, current_date, current_time, localtime",
     ["current_date", "current_time", "current_timestamp", "localtime", "localtimestamp"]),
    ("sql", "SELECT 'now'::timestamptz, timestamp 'NOW', date 'today', ' tomorrow '::date, 'yesterday'::timestamp",
     ["clock word in a string: ' tomorrow '", "clock word in a string: 'NOW'", "clock word in a string: 'now'", "clock word in a string: 'today'", "clock word in a string: 'yesterday'"]),
    ("sql", "SELECT 'now'::text, 'today'::varchar, pg_catalog.date '2026-11-14', 'now is the time'", ["clock word in a string: 'now is the time'", "clock word in a string: 'now'", "clock word in a string: 'today'"]),
    # A clock word in a string is reported, whatever type it is given or none (F4; once the Advisor's CK-2R3 cases).
    ("sql", "SELECT pg_catalog.timestamptz('now'), timestamptz('now'), pg_catalog.date('today')",
     ["clock word in a string: 'now'", "clock word in a string: 'today'"]),
    ("sql", "SELECT ('now'::text)::timestamptz", ["clock word in a string: 'now'"]),
    ("sql", "SELECT 'today'::public.\"date\", 'today'::myschema.timestamptz", ["clock word in a string: 'today'"]),
    ("sql", "SELECT 'today'::pg_catalog.date", ["clock word in a string: 'today'"]),
    # A clock word in a string with no type at all is reported the same way (F4).
    ("sql", "SELECT 'today'", ["clock word in a string: 'today'"]),
    ("sql", "INSERT INTO public.t (at) VALUES ('now')", ["clock word in a string: 'now'"]),
    ("sql", "-- clock_timestamp()\nSELECT 1 /* now() /* nested */ statement_timestamp() */", []),
    ("sql", 'SELECT pg_catalog."clock_timestamp"(), "now"(), pg_catalog . "statement_timestamp" ()',
     ["clock_timestamp()", "now()", "statement_timestamp()"]),
    ("sql", 'SELECT "Now"(), "clock_timestamp_"(), public.now(), x.now() FROM (SELECT 1) x', []),
    ("sql", 'SELECT U&"\\006Eow"()', ["now()"]),
    ("sql", "SELECT public.intevia_clock_statement(), public.intevia_clock_instant(), public.intevia_clock_transaction()", []),
    ("sql", "SELECT 'a -- b', transaction_timestamp ( )", ["transaction_timestamp()"]),
    # The Advisor's CK-2R2 cases, and their controls.
    ("sql", 'SELECT"now"()', ["now()"]),
    ("sql", 'SELECT"pg_catalog"."now"()', ["now()"]),
    ("sql", "SELECT\n-- stored report time\rnow()", ["now()"]),
    ("sql", "SELECT\n-- stored report time\r\nnow()", ["now()"]),
    ("sql", "SELECT E'abc'\n'it\\'s --' || now()::text", ["now()"]),
    ("sql", "SELECT $\U00010400$--$\U00010400$ || now()::text", ["now()"]),
    ("sql", "SELECT $a$--$a$ || now()::text", ["now()"]),
    ("sql", 'SELECT now() AS U&"example\\\\+FFFFFF"', ["now()"]),
    ("sql", 'SELECT public.intevia_clock_transaction() AS U&"example\\\\+FFFFFF"', []),
    # Text a routine may run as SQL is searched with the census tool's patterns; a quoted name inside it is only text.
    ("sql", "SELECT 'SELECT now()', $q$SELECT clock_timestamp()$q$", ["clock word in a string: 'SELECT now()'", "in a string: clock_timestamp()", "in a string: now()"]),
    ("sql", "SELECT 'SELECT \"now\"()', $q$SELECT \"now\"()$q$", ["clock word in a string: 'SELECT \"now\"()'"]),
    ("sql", "SELECT e'x', bee'now()'", ["clock word in a string: 'now()'", "in a string: now()"]),
    # A text the parser cannot read is reported, never passed over.
    ("sql", "SELECT 'open", ["unreadable as sql: unterminated quoted string at or near \"'open\" at position 8"]),
    ("expressions", "statement_timestamp(), 1", ["statement_timestamp()"]),
    ("table constraint", "CHECK ((recorded_at <= now()))", ["now()"]),
    ("domain constraint", "CHECK ((VALUE < CURRENT_TIMESTAMP))", ["current_timestamp"]),
    ("domain constraint", "NOT NULL", []),
    ("sql", "CREATE INDEX x ON public.t USING btree (a) WHERE (b < now())", ["now()"]),
    ("sql", "CREATE TRIGGER x BEFORE INSERT ON public.t FOR EACH ROW WHEN ((new.a < statement_timestamp())) "
            "EXECUTE FUNCTION public.f()", ["statement_timestamp()"]),
    ("sql body", "BEGIN ATOMIC\n SELECT clock_timestamp();\nEND", ["clock_timestamp()"]),
    # PL/pgSQL: every SQL text inside a routine is read, whatever statement holds it.
    ("plpgsql", plpgsql("""
DECLARE t timestamptz := clock_timestamp(); r record; n int;
BEGIN
  t := statement_timestamp();
  r.x := now();
  IF localtimestamp > t THEN RETURN; END IF;
  FOR r IN SELECT current_date AS c LOOP n := n + 1; END LOOP;
  FOR i IN 1..timeofday()::int LOOP END LOOP;
  EXECUTE format('SELECT %s', 'transaction_timestamp()') INTO t USING current_time;
  INSERT INTO x VALUES ('now'::timestamptz) RETURNING 1 INTO n;
  RAISE NOTICE 'at %', localtime;
  WHILE current_timestamp < t LOOP EXIT; END LOOP;
END"""), ["clock word in a string: 'now'", "clock_timestamp()", "current_date", "current_time", "current_timestamp", "in a string: transaction_timestamp()", "localtime", "localtimestamp", "now()", "statement_timestamp()", "timeofday()"]),
    ("plpgsql", plpgsql("BEGIN PERFORM '\"'; RETURN pg_catalog.\"now\"(); END"), ["now()"]),
    ("plpgsql", plpgsql("BEGIN PERFORM $q$\"$q$; RETURN pg_catalog.\"now\"(); END"), ["now()"]),
    ("plpgsql", plpgsql("BEGIN PERFORM $s$--$s$; RETURN clock_timestamp(); END"), ["clock_timestamp()"]),
    ("plpgsql", plpgsql("BEGIN PERFORM '\"'; RETURN public.intevia_clock_transaction(); END"), []),
    ("plpgsql", plpgsql("BEGIN EXECUTE 'SELECT now()'; EXECUTE 'SELECT \"now\"()'; END"), ["clock word in a string: 'SELECT \"now\"()'", "clock word in a string: 'SELECT now()'", "in a string: now()"]),
    # A RAISE message is a string constant (rule v2.1 section 1): its census pattern is reported (CK-2R10; before, the
    # message was not read). The comments are not strings and are not read.
    ("plpgsql", plpgsql("BEGIN -- now()\n /* clock_timestamp() */ RAISE NOTICE 'clock_timestamp()'; END"), ["in a string: clock_timestamp()"]),
    ("plpgsql", plpgsql("DECLARE t timestamp; BEGIN t := 'now'; RETURN; END"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE t text; BEGIN t := 'now'; t = 'today'; RETURN; END"), ["clock word in a string: 'now'", "clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("DECLARE t timestamp := 'now'; u text := 'now'; BEGIN RETURN; END"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE c CURSOR FOR SELECT now(); BEGIN RETURN; END"), ["now()"]),
    # An assignment's operator is found by reading, not by its first spelling: a quoted name may hold ":=".
    ("plpgsql", plpgsql('DECLARE "x:=y" timestamptz; BEGIN "x:=y" := now(); RETURN; END'), ["now()"]),
    ("plpgsql", plpgsql('DECLARE "x:=y" timestamptz; BEGIN "x:=y" := public.intevia_clock_statement(); RETURN; END'), []),
    ("plpgsql", plpgsql("DECLARE a timestamptz[]; BEGIN a[extract(epoch FROM now())::int] := NULL; RETURN; END"), ["now()"]),
    # A variable of a table's row type with a field assigned: read as PostgreSQL reads it.
    ("plpgsql", plpgsql("DECLARE v public.core_cqact; BEGIN v.act_uuid := gen_random_uuid(); v.effective_at := now(); END"),
     ["now()"]),
    ("plpgsql", plpgsql("DECLARE v public.core_cqact%ROWTYPE; BEGIN v.effective_at := public.intevia_clock_statement(); END"),
     []),
    ("plpgsql", plpgsql("\n-- v public.core_cqact;\nDECLARE v public.core_cqact;\nBEGIN v.effective_at := public.intevia_clock_statement(); END\n"),
     []),
    ("plpgsql", plpgsql("\n-- v public.core_cqact;\nDECLARE v public.core_cqact;\nBEGIN v.effective_at := now(); END\n"),
     ["now()"]),
    ("plpgsql", plpgsql("BEGIN v.effective_at := now(); END"),
     ["unreadable as plpgsql: \"v.effective_at\" is not a known variable at position 0"]),
    # The Advisor's CK-2R4 cases. Nothing excuses a clock word: every one is reported where it is written (F4).
    ("plpgsql", plpgsql("DECLARE t text;\nBEGIN\n  t := 'kept' WHERE CURRENT_TIMESTAMP IS NOT NULL;\n  RETURN;\nEND\n"),
     ["current_timestamp"]),
    ("plpgsql", plpgsql("DECLARE t text;\nBEGIN\n  t := 'kept' WHERE public.intevia_clock_transaction() IS NOT NULL;\n  RETURN;\nEND\n"),
     []),
    # A clock word is reported whatever its destination: a time, text, a field or a return (v2.1 section 4, rows 4 and 9).
    ("plpgsql", plpgsql("DECLARE t timestamp; BEGIN t := 'now'::text; RETURN; END"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE t text; BEGIN t := 'now'::text; RETURN; END"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE t timestamptz := 'today'::varchar; BEGIN RETURN; END"),
     ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("DECLARE v public.core_cqact; BEGIN v.effective_at := 'now'::text; END"),
     ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("BEGIN RETURN 'now'::text; END", "timestamp without time zone"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("BEGIN RETURN 'now'::text; END", "text"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("BEGIN RETURN 'now'; END", "text"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("BEGIN RETURN 'now'; END", "timestamp with time zone"), ["clock word in a string: 'now'"]),
    # Statements whose rows are assigned to variables, cursors and PERFORM: each clock word is reported (pairs 3, 5).
    ("plpgsql", plpgsql("DECLARE t timestamp; BEGIN SELECT 'now'::text INTO t; RETURN; END"),
     ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE t text; BEGIN SELECT 'now'::text INTO STRICT t; RETURN; END"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE t text; u date; BEGIN SELECT 'now'::text, 'today'::text INTO t, u; RETURN; END"),
     ["clock word in a string: 'now'", "clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("DECLARE t timestamp; BEGIN SELECT 1 UNION SELECT 'now'::text INTO t; RETURN; END"),
     ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE t timestamp; BEGIN INSERT INTO x VALUES (1) RETURNING 'now'::text INTO t; RETURN; END"),
     ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE t timestamp; BEGIN FOR t IN SELECT 'now'::text LOOP NULL; END LOOP; END"),
     ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE t text; BEGIN FOR t IN VALUES ('now'::text) LOOP NULL; END LOOP; END"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE r record; BEGIN SELECT 'now'::text INTO r; RETURN; END"),
     ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE c CURSOR FOR SELECT 'now'::text; BEGIN RETURN; END"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE c refcursor; BEGIN OPEN c FOR SELECT 'now'::text; END"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE c refcursor; BEGIN OPEN c FOR SELECT '2000-01-01'::text; END"), []),
    ("plpgsql", plpgsql("BEGIN PERFORM 'now'::text; RETURN; END"), ["clock word in a string: 'now'"]),
    # A cast in either form, and a one-argument function, change nothing: the clock word is reported (pairs 1, 2).
    ("plpgsql", plpgsql("BEGIN RETURN timestamptz('now'::text); END", "timestamp with time zone"), ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("DECLARE t timestamp; BEGIN t := lower('NOW'::text); RETURN; END"),
     ["clock word in a string: 'NOW'"]),
    ("plpgsql", plpgsql("DECLARE t text; BEGIN t := lower('NOW'::text); RETURN; END"), ["clock word in a string: 'NOW'"]),
    # The operator stands in code: an "=" in a comment is not the operator, however both sides then read.
    ("plpgsql", plpgsql("DECLARE t boolean;\nBEGIN\n  t -- = 1 /*\n    = CURRENT_TIMESTAMP -- */\n      IS NOT NULL;\n  RETURN;\nEND\n"),
     ["current_timestamp"]),
    ("plpgsql", plpgsql("DECLARE t boolean;\nBEGIN\n  t -- = 1 /*\n    = public.intevia_clock_transaction() -- */\n      IS NOT NULL;\n"
                        "  RETURN;\nEND\n"), []),
    ("plpgsql", plpgsql("DECLARE t boolean;\nBEGIN\n  t /* := 1 */ := now() IS NOT NULL;\n  RETURN;\nEND\n"), ["now()"]),
    # Only a row type the catalogue holds is read as a record: a scalar, or a name it does not hold, has no fields.
    ("plpgsql", plpgsql("DECLARE v integer;\nBEGIN v.effective_at := 1; END\n"),
     ["unreadable as plpgsql: \"v.effective_at\" is not a known variable at position 0"]),
    ("plpgsql", plpgsql("DECLARE v public.not_a_row_type;\nBEGIN v.effective_at := now(); END\n"),
     ["unreadable as plpgsql: \"v.effective_at\" is not a known variable at position 0"]),
    ("plpgsql", plpgsql("DECLARE v core_cqact; w public.core_cqact;\nBEGIN v.effective_at := now(); w.effective_at := clock_timestamp(); END\n"),
     ["clock_timestamp()", "now()"]),
    # The Advisor's CK-2R5 case: RETURN NEXT, whatever the routine's element type.
    ("plpgsql", plpgsql("BEGIN\n    RETURN NEXT 'now'::text;\n    RETURN;\nEND\n", "SETOF timestamp without time zone"),
     ["clock word in a string: 'now'"]),
    ("plpgsql", plpgsql("BEGIN\n    RETURN NEXT '2000-01-01'::text;\n    RETURN;\nEND\n", "SETOF timestamp without time zone"), []),
    ("plpgsql", plpgsql("BEGIN\n    RETURN NEXT 'now'::text;\n    RETURN;\nEND\n", "SETOF text"), ["clock word in a string: 'now'"]),
    ("a routine in c", "intevia_clock_c", ["unreadable as a routine in c: 'a routine in c'"]),
)

# The CK-2R2 matrix, kept: a quote mark or a comment mark of one kind inside text of another kind, standing before
# a call, after it, and both. Every text is a SELECT list PostgreSQL parses; the one read is the name called.
LEXICAL_FORMS = (
    ("a single-quoted string", "'a \" -- /* '' b'", "'c '' \" */ -- d'"),
    ("an escape string", "E'a \\' \" -- /* b'", "E'c \\\\' || E'\\' \" d'"),
    ("a quoted identifier", '"a \' -- /* "" $q$ b"', '"c "" \' */ d"'),
    ("a coded quoted identifier", 'U&"a \' \\0022 -- b"', 'U&"c \\+000027 /* d"'),
    ("dollar-quoted text", "$q$a ' \" -- /* b$q$", "$$c \" ' */ d$$"),
)
COMMENT_FORMS = (
    ("a line comment", "-- a ' \" $q$ /* %(decoy)s()\n", "-- c ' \" $q$ %(decoy)s()"),
    ("a block comment", "/* a ' \" /* -- %(decoy)s() */ $q$ b */", "/* c \" ' /* */ %(decoy)s() */"),
)
CALL_FORMS = ("%s()", 'pg_catalog."%s"()', '"%s" ()')
BESIDE_A_CALL = tuple(
    ("sql", shape % {"before": before % {"decoy": decoy}, "after": after % {"decoy": decoy}, "call": call % name},
     [name + "()"])
    for position, name in enumerate(CALLED_BY_NAME)
    for decoy in (CALLED_BY_NAME[(position + 1) % len(CALLED_BY_NAME)],)
    for forms, shapes in (
        (LEXICAL_FORMS, ("SELECT %(before)s x, %(call)s", "SELECT %(call)s, %(after)s y",
                         "SELECT %(before)s x, %(call)s, %(after)s y")),
        (COMMENT_FORMS, ("SELECT %(before)s 1 x, %(call)s", "SELECT %(call)s %(after)s",
                         "SELECT %(before)s 1 x, %(call)s %(after)s")),
    )
    for _kind, before, after in forms
    for call in CALL_FORMS
    for shape in shapes
)

# A routine's texts are read one by one. Each line: language, body, SQL-standard body, argument defaults, definition;
# and the reads.
FIELDS = (
    (("sql", "SELECT $1 -- supplied time", None, "statement_timestamp()", None), ["statement_timestamp()"]),
    (("sql", "SELECT $1 -- supplied time", None, "public.intevia_clock_statement()", None), []),
    (("sql", "SELECT 1 /* a comment */", None, "now(), 2", None), ["now()"]),
    (("sql", "", "BEGIN ATOMIC\n SELECT clock_timestamp();\nEND", None, None), ["clock_timestamp()"]),
    (("plpgsql", "BEGIN RETURN; END", None, "now()", plpgsql("BEGIN RETURN; END")), ["now()"]),
)


# The Advisor's CK-2R9 rule-review cases (her M, K, C, P and S cases), each with its verdict under the acceptance rule
# v2.1 (section 4). Every one was run on PostgreSQL 17.11 by the VC first. Each line: the form, the text, the reads.
ADVISOR_CK2R9 = (
    # M1: letters, not digits or "_", end a clock word. M2, M3, M4, M8: the written word is reported wherever it goes.
    ("sql", "SELECT 'tomorrow10:00'::timestamp", ["clock word in a string: 'tomorrow10:00'"]),
    ("sql", "SELECT '_today_'::date", ["clock word in a string: '_today_'"]),
    ("plpgsql", plpgsql("\nDECLARE label text := 'today';\nBEGIN RETURN label::date; END\n", "date"), ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nBEGIN RETURN 'today'::text; END\n", "text"), ["clock word in a string: 'today'"]),
    ("sql", "\nSELECT r9_label()::date\n", []),
    ("plpgsql", plpgsql("\nDECLARE x text;\nBEGIN SELECT 'today' INTO x UNION ALL SELECT DATE '2001-01-01' WHERE false; RETURN x; END\n", "text"),
     ["clock word in a string: 'today'"]),
    ("sql", "\nSELECT (s::date)::text\n", []),
    ("plpgsql", plpgsql("\nDECLARE s text := 'tomorrow';\nBEGIN RETURN r9_normalise(s); END\n", "text"), ["clock word in a string: 'tomorrow'"]),
    # M5: a domain's check holds no clock word; the routine that assigns one is reported.
    ("domain constraint", "CHECK (VALUE::date IS NOT NULL)", []),
    ("plpgsql", plpgsql("\nDECLARE label r9_day_label := 'today';\nBEGIN RETURN label; END\n", "text"), ["clock word in a string: 'today'"]),
    # M6: a clock word inside an array, range, row or JSON value.
    ("sql", "SELECT ('{today,2001-01-01}'::date[])[1]", ["clock word in a string: '{today,2001-01-01}'"]),
    ("sql", "SELECT (ARRAY['today'::date, DATE '2001-01-01'])[1]", ["clock word in a string: 'today'"]),
    ("sql", "SELECT lower('[today,tomorrow)'::daterange)", ["clock word in a string: '[today,tomorrow)'"]),
    ("sql", "SELECT ('(urgent,today)'::r9_row).d", ["clock word in a string: '(urgent,today)'"]),
    ("sql", "SELECT d FROM jsonb_to_record('{\"label\":\"urgent\",\"d\":\"today\"}'::jsonb) AS v(label text, d date)",
     ["clock word in a string: '{\"label\":\"urgent\",\"d\":\"today\"}'"]),
    # K1: every spelling the parser decodes to a clock word is reported (its first fifteen values); the special inputs that
    # read no clock (epoch, infinity, allballs) give no finding.
    ("sql", "SELECT 'today'", ["clock word in a string: 'today'"]),
    ("sql", "SELECT $q$today$q$", ["clock word in a string: 'today'"]),
    ("sql", "SELECT E'to\\x64ay'", ["clock word in a string: 'today'"]),
    ("sql", "SELECT U&'to\\0064ay'", ["clock word in a string: 'today'"]),
    ("sql", "SELECT 'to'\n   'day'", ["clock word in a string: 'today'"]),
    ("sql", "SELECT ' NoW '", ["clock word in a string: ' NoW '"]),
    ("sql", "SELECT 'tomorrow 10:00'", ["clock word in a string: 'tomorrow 10:00'"]),
    ("sql", "SELECT 'yesterday allballs'", ["clock word in a string: 'yesterday allballs'"]),
    ("sql", "SELECT 'due:today'", ["clock word in a string: 'due:today'"]),
    ("sql", "SELECT 'Due today'", ["clock word in a string: 'Due today'"]),
    ("sql", "SELECT '{today,2001-01-01}'", ["clock word in a string: '{today,2001-01-01}'"]),
    ("sql", "SELECT '[today,tomorrow)'", ["clock word in a string: '[today,tomorrow)'"]),
    ("sql", "SELECT '(urgent,today)'", ["clock word in a string: '(urgent,today)'"]),
    ("sql", "SELECT '{\"label\":\"urgent\",\"d\":\"today\"}'", ["clock word in a string: '{\"label\":\"urgent\",\"d\":\"today\"}'"]),
    ("sql", "SELECT 'today [local]'", ["clock word in a string: 'today [local]'"]),
    ("sql", "SELECT 'epoch'", []),
    ("sql", "SELECT 'infinity'", []),
    ("sql", "SELECT 'allballs'", []),
    # A plain string is read one fixed way, with standard_conforming_strings on: its backslash is a character, so
    # 'to\x64ay' holds no clock word (the server agrees under the default setting; an E'' string decodes the escape).
    ("sql", "SELECT 'to\\x64ay'", []),
    # M7 and M11: a word made by computing on text is a stated limit (v2.1 section 5); a word written is reported.
    ("sql", "SELECT split_part('due:today', ':', 2)::date", ["clock word in a string: 'due:today'"]),
    ("sql", "SELECT ('today [local]'::varchar(5))::date", ["clock word in a string: 'today [local]'"]),
    ("sql", "SELECT (('to'::text || 'day')::date)", []),
    ("sql", "SELECT lower('TODAY'::text)::date", ["clock word in a string: 'TODAY'"]),
    ("sql", "SELECT ('todayish'::varchar(5))::date", []),
    # M9, M12 and S3: one-argument age, and timetz in any form.
    ("sql", "SELECT age(TIMESTAMP '2000-01-01')", ["age() with one argument"]),
    ("sql", "SELECT pg_catalog.age(TIMESTAMP '2000-01-01'), age(TIMESTAMP '2001-01-01', TIMESTAMP '2000-01-01')",
     ["age() with one argument"]),
    ("sql", "SELECT age(CURRENT_DATE::timestamp, TIMESTAMP '2000-01-01')", ["current_date"]),
    ("sql", "SELECT TIMETZ '12:00:00+00' AT TIME ZONE 'America/New_York'", ["timetz"]),
    ("sql", "SELECT ('12:00'::text)::timetz, ('12:00-05'::text)::time with time zone", ["timetz"]),
    ("sql", "SELECT timezone('Europe/London', timetz('12:00:00+00'))", ["timetz"]),
    ("sql", "SELECT NULL::timetz[]", ["timetz"]),
    ("sql", "CREATE TABLE r9_shift (starts time with time zone)", ["timetz"]),
    ("plpgsql", plpgsql("\nDECLARE t timetz;\nBEGIN RETURN; END\n"), ["timetz"]),
    ("sql", "SELECT TIME '12:00', NULL::timestamptz", []),
    # A type of that name in another schema is not PostgreSQL's timetz.
    ("sql", "SELECT NULL::app.timetz, app.timetz('12:00')", []),
    # M10: no setting is consulted.
    ("sql", "SELECT 'tomorrow 10:00 WST'::timestamptz", ["clock word in a string: 'tomorrow 10:00 WST'"]),
    # K4, K2, K3: positions, repeats and statements change nothing.
    ("plpgsql", plpgsql("\nDECLARE n bigint; x text;\nBEGIN SELECT count(*), 'today'::text INTO n,x FROM (VALUES (1),(2)) v(i); RETURN x; END\n", "text"),
     ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nDECLARE label text; stamp timestamp;\nBEGIN SELECT 'today'::text, 'today'::text INTO label, stamp;\n      RETURN stamp;\nEND\n",
                        "timestamp without time zone"), ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nDECLARE label text;\nBEGIN\n  INSERT INTO r9_audit VALUES ('today') RETURNING 'today'::text INTO label;\n  RETURN label;\nEND\n", "text"),
     ["clock word in a string: 'today'"]),
    # C1 to C7: harmless labels are reported (the cost the Human Governor accepted; an exact allow-list entry clears one).
    ("sql", "SELECT 'today'::text AS tab_label, 'tomorrow'::text IN ('today'::text, 'tomorrow'::text), "
            "jsonb_build_object('when', 'now'::text), format('Show %s', 'today'::text), "
            "CASE WHEN false THEN 'yesterday'::text ELSE 'archive' END",
     ["clock word in a string: 'now'", "clock word in a string: 'today'", "clock word in a string: 'tomorrow'",
      "clock word in a string: 'yesterday'"]),
    ("expressions", "'today'::text", ["clock word in a string: 'today'"]),
    ("table constraint", "CHECK (label = ANY (ARRAY['today'::text, 'tomorrow'::text, 'manual'::text]))",
     ["clock word in a string: 'today'", "clock word in a string: 'tomorrow'"]),
    ("sql", "CREATE INDEX r9_today_only ON public.r9_preferences USING btree (label) WHERE (label = 'today'::text)",
     ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nDECLARE r record; label text; cur CURSOR FOR SELECT 'today'::text;\nBEGIN\n  SELECT 'today'::text INTO r;\n"
                        "  OPEN cur; FETCH cur INTO label; CLOSE cur;\n  PERFORM 'today'::text;\n  RAISE NOTICE 'Display %', 'today'::text;\nEND\n"),
     ["clock word in a string: 'today'"]),
    ("sql", "SELECT 'today'::integer", ["clock word in a string: 'today'"]),
    ("sql", "SELECT r9_epoch_label('today'::text)::date", ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nDECLARE x text;\nBEGIN x := ('today'::r9_bucket)::text; RETURN x; END\n", "text"), ["clock word in a string: 'today'"]),
    # P1 to P7: each pair shares one verdict.
    ("plpgsql", plpgsql("\nDECLARE a integer; b text; x text;\nBEGIN SELECT q.*, 'today'::text INTO a,b,x FROM (VALUES (1,'b'::text)) q(i,s); RETURN x; END\n", "text"),
     ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nDECLARE x text;\nBEGIN x := pg_catalog.text('today'); RETURN x; END\n", "text"), ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nDECLARE x text;\nBEGIN\n  FOR x IN VALUES ('today'), (DATE '2001-01-01') LOOP RETURN x; END LOOP;\n  RETURN NULL;\nEND\n", "text"),
     ["clock word in a string: 'today'"]),
    ("sql", " SELECT 'today'::text ", ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nBEGIN RETURN QUERY SELECT 'today'::text; END\n", "SETOF text"), ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nDECLARE x text;\nBEGIN x := coalesce(NULL::text,'today'::text); RETURN x; END\n", "text"),
     ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("\nDECLARE n integer; x text;\nBEGIN SELECT 2*3, 'today'::text INTO n,x; RETURN x; END\n", "text"),
     ["clock word in a string: 'today'"]),
    ("sql", "SELECT 'Due today'::text, 'S015 diagnostic now=%s'::text",
     ["clock word in a string: 'Due today'", "clock word in a string: 'S015 diagnostic now=%s'"]),
    # P8: a view's literal is folded when the view is created (a stated limit); the routine's text is reported.
    ("sql", " SELECT '2026-11-14'::date AS d;", []),
    ("sql", " SELECT DATE 'today' ", ["clock word in a string: 'today'"]),
    # S1 and S2: a caller's data is a stated limit; a domain default and a policy are read (and witnessed below).
    ("sql", " SELECT s::date ", []),
    ("expressions", "('today'::text)::date", ["clock word in a string: 'today'"]),
    ("expressions", "(d <= ('today'::text)::date)", ["clock word in a string: 'today'"]),
    # K1: a clock word however it is quoted, read as the parser decodes it; a fixed special value is not a clock word.
    ("sql", "SELECT $q$today$q$, E'to\\x64ay', U&'to\\0064ay', 'to'\n'day', ' NoW '",
     ["clock word in a string: ' NoW '", "clock word in a string: 'today'"]),
    ("sql", "SELECT 'epoch'::timestamp, 'infinity'::date, 'allballs'::time, 'known', 'nowhere'", []),
)

# Gemini's CK-2R10 step 2 defects D1 and D3 (owed ledger C1-130, item 9), corrected at CK-2j1a. PostgreSQL 17.11's own
# reading of each text is the VC's (claim check of 14 Nov 2026): after the comment that a lone carriage return ends,
# it reads the string 'today'; and it returns 'hello' under the column name uescape_alias. Each line: the form, the
# text, the reads.
GEMINI_CK2R10 = (
    # D1: a "--" comment ends at a carriage return, so the string after it is written in code and is read.
    ("sql", "NOTIFY r9, -- comment\r'today'", ["clock word in a string: 'today'"]),
    ("sql", "SELECT 1 -- c\r, 'today'", ["clock word in a string: 'today'"]),
    # D3: a name that only starts with UESCAPE is a name. The string before it is decoded with the default escape.
    ("sql", "SELECT U&'hello' uescape_alias", []),
    ("sql", "SELECT U&'to\\0064ay' uescape_alias", ["clock word in a string: 'today'"]),
)

# The Advisor's CK-2R9 final-check cases and their families (CK-2R10), each with its verdict under rule v2.1. Every
# string constant written in a text is read, wherever the grammar keeps it (finding 2); the clock words and their
# boundaries are ASCII (finding 3). Each line: the form, the text, the reads.
ADVISOR_CK2R10 = (
    # Finding 2: a RAISE message, a NOTIFY payload, and the other places the grammar keeps a string outside a constant.
    ("plpgsql", plpgsql("BEGIN RAISE NOTICE 'today'; END"), ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("BEGIN RAISE NOTICE 'now()'; END"), ["clock word in a string: 'now()'", "in a string: now()"]),
    ("plpgsql", plpgsql("BEGIN RAISE NOTICE '%', 'today'; END"), ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("BEGIN RAISE EXCEPTION E'to\\x64ay'; END"), ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("BEGIN RAISE NOTICE 'to'\n  'day'; END"), ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("BEGIN RAISE NOTICE $m$Yesterday$m$; END"), ["clock word in a string: 'Yesterday'"]),
    ("plpgsql", plpgsql("BEGIN RAISE NOTICE '%', U&'to\\0064ay'; END"), ["clock word in a string: 'today'"]),
    ("plpgsql", plpgsql("BEGIN RAISE NOTICE 'checked'; -- 'today'\n END"), []),
    ("sql", "NOTIFY r9, 'today'", ["clock word in a string: 'today'"]),
    ("sql", "COMMENT ON TABLE r9.x IS 'tomorrow'", ["clock word in a string: 'tomorrow'"]),
    ("sql", "CREATE TYPE r9.e AS ENUM ('today', 'later')", ["clock word in a string: 'today'"]),
    ("sql", "ALTER TYPE r9.e ADD VALUE 'yesterday'", ["clock word in a string: 'yesterday'"]),
    ("sql", "CREATE FUNCTION r9.inner() RETURNS date LANGUAGE sql AS 'SELECT ''today''::date'",
     ["clock word in a string: 'SELECT 'today'::date'"]),
    ("sql", "COPY r9.x FROM 'today.csv'", ["clock word in a string: 'today.csv'"]),
    ("sql", "SET application_name TO 'tomorrow'", ["clock word in a string: 'tomorrow'"]),
    ("sql", "CREATE TABLE r9.x (a text) WITH (fillfactor = 70)", []),
    # Not strings: comments, quoted and Unicode-escaped names, bit strings, parameters and names holding "$".
    ("sql", "SELECT 1 -- 'today'\n", []),
    ("sql", "SELECT 1 /* 'today' /* nested 'now' */ */", []),
    ("sql", "SELECT 1-- 'today'\n", []),
    ("sql", "SELECT 1/* 'today' */", []),
    ("sql", 'SELECT 1 AS "today", U&"t\\0061ble" FROM r9.x', []),
    ("sql", "SELECT B'1010', X'0f', $1, a$b FROM r9.x", []),
    # Quoting, decoded as PostgreSQL decodes it.
    ("sql", "SELECT 'it''s today'", ["clock word in a string: 'it's today'"]),
    ("sql", "SELECT E'\\'today\\''", ["clock word in a string: ''today''"]),
    # A string continues after whitespace holding a newline (PostgreSQL's lexer, quotecontinue).
    ("sql", "SELECT U&'d!0061y' UESCAPE '!', 'to'\n \t 'morrow'", ["clock word in a string: 'tomorrow'"]),
    # PostgreSQL 17.11 also continues a string across a "--" comment (it read 'to' -- c / 'morrow' as 'tomorrow'), but
    # libpg_query 17 does not parse it: the text is reported unreadable, which fails the check (F7, fail closed).
    ("sql", "SELECT 'to' -- c\n 'morrow'", ["unreadable as sql: syntax error at or near \"'morrow'\" at position 19"]),
    # Finding 3: the word and its boundaries are ASCII. U+212A KELVIN SIGN and an accented letter are boundaries; U+017F
    # LONG S is not an s.
    ("sql", "SELECT 'Ktoday'", ["clock word in a string: 'Ktoday'"]),
    ("sql", "SELECT 'yeſterday'", []),
    ("sql", "SELECT 'étoday'", ["clock word in a string: 'étoday'"]),
    ("sql", "SELECT 'TODAY', 'ToMoRrOw'", ["clock word in a string: 'TODAY'", "clock word in a string: 'ToMoRrOw'"]),
)


TYPE_IDENTITY_SETUP = (
    "CREATE SCHEMA clk05_types", "CREATE DOMAIN clk05_types.text AS timetz",
    "CREATE SCHEMA clk05_row", "CREATE DOMAIN clk05_row.probe AS integer", 'CREATE TYPE clk05_row."PROBE" AS (x integer)',
    "CREATE SCHEMA clk05_first", "CREATE SCHEMA clk05_second",
    "CREATE DOMAIN clk05_first.status AS integer", "CREATE TYPE clk05_second.status AS (x integer)",
)
_SHADOW = " SET search_path TO 'clk05_types', 'pg_catalog'\n"
_HIDDEN = " SET search_path TO 'clk05_first', 'clk05_second', 'pg_catalog'\n"
_FIELD_UNREADABLE = ["unreadable as plpgsql: \"v.x\" is not a known variable at position 0"]
# A type is the one the catalogue resolves for the routine, through its domains (F6, and the row-type retry): a domain
# over timetz named like a built-in, a composite named like a domain but for its case, and a composite hidden behind
# an earlier schema. Each line: a routine's texts, read with the catalogue's types; and the reads.
TYPE_IDENTITY = (
    ([("plpgsql", plpgsql("DECLARE t text;\nBEGIN\n    RETURN;\nEND\n", "void", _SHADOW))], ["timetz"]),
    ([("plpgsql", plpgsql("BEGIN\n    PERFORM NULL::text;\n    RETURN;\nEND\n", "void", _SHADOW))], ["timetz"]),
    ([("plpgsql", plpgsql("DECLARE t pg_catalog.text;\nBEGIN\n    PERFORM NULL::pg_catalog.text;\n    RETURN;\nEND\n", "void",
                          _SHADOW))], []),
    # pg_catalog, when the path does not name it, is searched before the path: here ``text`` is PostgreSQL's own.
    ([("plpgsql", plpgsql("DECLARE t text;\nBEGIN\n    PERFORM NULL::text;\n    RETURN;\nEND\n", "void",
                          " SET search_path TO 'clk05_types'\n"))], []),
    ([("plpgsql", plpgsql("DECLARE v clk05_row.probe;\nBEGIN v.x := 1; END\n"))], _FIELD_UNREADABLE),
    ([("plpgsql", plpgsql("DECLARE v status;\nBEGIN v.x := 1; END\n", "void", _HIDDEN))], _FIELD_UNREADABLE),
    ([("plpgsql", plpgsql("DECLARE v clk05_second.status;\nBEGIN v.x := now(); END\n", "void", _HIDDEN))], ["now()"]),
    ([("plpgsql", plpgsql("DECLARE v public.core_cqact;\nBEGIN v.effective_at := now(); END\n"))], ["now()"]),
)
# Installed routines read as the catalogue scan reads them (``function_readings``): a routine's own search path, a
# SECURITY DEFINER routine's owner as its ``$user``, and ``x%ROWTYPE`` as the row of the relation the catalogue finds.
ROUTINE_IDENTITY_SETUP = (
    "CREATE SCHEMA clk05_idx", "CREATE SCHEMA clk05_rows",
    "CREATE DOMAIN clk05_types.moment AS timetz",
    "CREATE TABLE clk05_idx.holder (x integer)", "CREATE INDEX item ON clk05_idx.holder (x)",
    "CREATE TABLE clk05_rows.item (x integer)",
    "CREATE SCHEMA clk05_owner", "CREATE DOMAIN clk05_owner.activity_tz AS timetz",
    "CREATE TABLE clk05_types.anchor (t timetz, n integer)",
    "SET check_function_bodies = off",   # PostgreSQL itself cannot find the first %ROWTYPE's relation
)
_ROUTINE = "CREATE FUNCTION clk05_types.%s() RETURNS %s LANGUAGE plpgsql%s AS $f$%s$f$"
_LOCAL = " SET search_path TO clk05_types, pg_catalog"
_INDEX_FIRST = " SET search_path TO clk05_idx, clk05_rows, pg_catalog"
# Each line: the routine's name, and how it is created; and the reads.
ROUTINE_IDENTITY = (
    ("cast_moment", _ROUTINE % ("cast_moment", "text", "", "BEGIN RETURN NULL::clk05_types.moment::text; END"), ["timetz"]),
    ("call_moment", _ROUTINE % ("call_moment", "text", "", "BEGIN RETURN clk05_types.moment(NULL::text)::text; END"), ["timetz"]),
    ("local_moment", _ROUTINE % ("local_moment", "text", _LOCAL, "DECLARE t moment; BEGIN RETURN NULL; END"), ["timetz"]),
    ("local_integer", _ROUTINE % ("local_integer", "text", _LOCAL, "DECLARE t integer; BEGIN RETURN NULL; END"), []),
    ("clock_word", _ROUTINE % ("clock_word", "date", "", "DECLARE label text := 'today'; BEGIN RETURN label::date; END"),
     ["clock word in a string: 'today'"]),
    ("fixed", _ROUTINE % ("fixed", "text", "", "DECLARE label text := '2000-01-01'; BEGIN RETURN label; END"), []),
    # A variable declared through %TYPE has the type of what it names: a column, or another variable (the Advisor's
    # CK-2R9 final check, finding 4).
    ("anchored", _ROUTINE % ("anchored", "text", "", "DECLARE a clk05_types.anchor.t%TYPE; BEGIN RETURN NULL; END"), ["timetz"]),
    ("anchored_integer", _ROUTINE % ("anchored_integer", "text", "", "DECLARE a clk05_types.anchor.n%TYPE; BEGIN RETURN NULL; END"), []),
    ("anchored_local", _ROUTINE % ("anchored_local", "text", _LOCAL, "DECLARE a anchor.t%TYPE; BEGIN RETURN NULL; END"), ["timetz"]),
    ("anchored_variable", _ROUTINE % ("anchored_variable", "text", "",
                                      "DECLARE b integer; a b%TYPE; BEGIN RETURN NULL; END"), []),
    ("anchored_label", _ROUTINE % ("anchored_label", "text", "",
                                   "<<blk>> DECLARE b integer; a blk.b%TYPE; BEGIN RETURN NULL; END"), []),
    # An anchor the catalogue cannot resolve (installable only with check_function_bodies off) is reported unreadable.
    ("anchored_missing", _ROUTINE % ("anchored_missing", "text", "",
                                     "DECLARE a clk05_types.nothing.t%TYPE; BEGIN RETURN NULL; END"),
     ["unreadable: %TYPE anchor not resolved: clk05_types.nothing.t%TYPE"]),
    # Gemini's CK-2R10 step 2 defect D2 (CK-2j1a): an anchor that is not resolved is not read as a variable sharing
    # its last name, whether that is PL/pgSQL's own FOUND, a declared variable, or a variable under a label the
    # routine does not hold. PostgreSQL 17.11 refuses each of these at CREATE with function checks on.
    ("anchored_missing_found", _ROUTINE % ("anchored_missing_found", "text", "",
                                           "DECLARE a clk05_types.nothing.found%TYPE; BEGIN RETURN NULL; END"),
     ["unreadable: %TYPE anchor not resolved: clk05_types.nothing.found%TYPE"]),
    ("anchored_missing_t", _ROUTINE % ("anchored_missing_t", "text", "",
                                       "DECLARE t integer; a clk05_types.nothing.t%TYPE; BEGIN RETURN NULL; END"),
     ["unreadable: %TYPE anchor not resolved: clk05_types.nothing.t%TYPE"]),
    ("anchored_no_label", _ROUTINE % ("anchored_no_label", "text", "",
                                      "DECLARE b integer; a nothing.b%TYPE; BEGIN RETURN NULL; END"),
     ["unreadable: %TYPE anchor not resolved: nothing.b%TYPE"]),
    ("row_index_first", _ROUTINE % ("row_index_first", "void", _INDEX_FIRST,
                                    "DECLARE v item%ROWTYPE; BEGIN v.x := 1; END"), _FIELD_UNREADABLE),
    ("row_qualified", _ROUTINE % ("row_qualified", "void", _INDEX_FIRST,
                                  "DECLARE v clk05_rows.item%ROWTYPE; BEGIN v.x := now(); END"), ["now()"]),
    # A SECURITY DEFINER routine's "$user" is its owner. The routine is installed under the reading role, and read
    # with its row's owner given as clk05_owner, a schema holding a domain over timetz; with the reading role as
    # owner, the same row names no such domain. (The VC's run installs it under a second role.)
    ("definer", _ROUTINE.replace("clk05_types.%s()", "clk05_owner.%s()") % (
        "definer", "text", ' SECURITY DEFINER SET search_path TO "$user", pg_catalog',
        "BEGIN PERFORM NULL::activity_tz; RETURN NULL; END"), ["timetz"]),
)
ROUTINE_ROW = dict(CATALOGUE)["function"] + " AND p.oid = {oid}"
# The places CK-2R9 adds, each witnessed by an object of its own (v2.1 section 2), and what each reads.
PLACES_SETUP = (
    "CREATE SCHEMA clk05_places",
    "CREATE DOMAIN clk05_places.day AS date DEFAULT ('today'::text)::date",
    "CREATE TABLE clk05_places.visible (d date)",
    "CREATE POLICY cut ON clk05_places.visible USING (d <= ('today'::text)::date) WITH CHECK (d > ('yesterday'::text)::date)",
    "CREATE TABLE clk05_places.log (d date, label text)",
    "CREATE RULE keep AS ON INSERT TO clk05_places.log DO ALSO SELECT pg_catalog.now()",
    "CREATE AGGREGATE clk05_places.latest(timestamptz) (SFUNC = pg_catalog.timestamptz_larger, STYPE = timestamptz, INITCOND = 'now')",
    # A moving aggregate's own initial value is a second place.
    "CREATE AGGREGATE clk05_places.joined(text) (SFUNC = pg_catalog.textcat, STYPE = text, MSFUNC = pg_catalog.textcat, "
    "MINVFUNC = pg_catalog.textcat, MSTYPE = text, MINITCOND = 'yesterday')",
    # A row filter may hold only immutable expressions, so it cannot read the clock; a clock word in it is still reported.
    "CREATE PUBLICATION clk05_pub FOR TABLE clk05_places.log WHERE (label <> 'tomorrow') WITH (publish = 'insert')",
    "CREATE TABLE clk05_places.shift (starts timetz)",
    "CREATE FUNCTION clk05_places.opening() RETURNS timetz LANGUAGE sql AS $f$ SELECT NULL::pg_catalog.timetz $f$",
    "CREATE DOMAIN clk05_places.wall AS timetz",
)
# Objects that are members of an extension are not read (rule v2.1 section 2), in every place: each object below would
# be reported were it not a member of plpgsql (the Advisor's CK-2R9 final check, finding 5).
EXTENSION_SETUP = (
    "CREATE SCHEMA clk05_ext",
    "CREATE TABLE clk05_ext.held (d date DEFAULT ('today'::text)::date, label text CHECK (label <> 'today'), starts timetz)",
    "CREATE INDEX held_today ON clk05_ext.held (d) WHERE label = 'today'",
    "CREATE VIEW clk05_ext.shown AS SELECT 'today'::text AS label",
    "CREATE DOMAIN clk05_ext.day AS date DEFAULT ('today'::text)::date",
    "CREATE FUNCTION clk05_ext.said() RETURNS text LANGUAGE sql AS $f$ SELECT 'today'::text $f$",
    "CREATE FUNCTION clk05_ext.fired() RETURNS trigger LANGUAGE plpgsql AS $f$BEGIN RETURN NEW; END$f$",
    "CREATE TRIGGER fired BEFORE INSERT ON clk05_ext.held FOR EACH ROW WHEN (NEW.label = 'yesterday') EXECUTE FUNCTION clk05_ext.fired()",
    "CREATE POLICY seen ON clk05_ext.held USING (label <> 'tomorrow')",
    "CREATE RULE kept AS ON INSERT TO clk05_ext.held DO ALSO SELECT pg_catalog.now()",
    "CREATE AGGREGATE clk05_ext.latest(timestamptz) (SFUNC = pg_catalog.timestamptz_larger, STYPE = timestamptz, INITCOND = 'now')",
    "CREATE PUBLICATION clk05_extpub FOR TABLE clk05_ext.held WHERE (label <> 'today') WITH (publish = 'insert')",
)
# (kind, name, catalogue, resolver): each is made a member of plpgsql, and released before it is dropped.
EXTENSION_MEMBERS = (
    ("TABLE", "clk05_ext.held", "pg_class", "to_regclass"),
    ("VIEW", "clk05_ext.shown", "pg_class", "to_regclass"),
    ("DOMAIN", "clk05_ext.day", "pg_type", "to_regtype"),
    ("FUNCTION", "clk05_ext.said()", "pg_proc", "to_regprocedure"),
    ("FUNCTION", "clk05_ext.fired()", "pg_proc", "to_regprocedure"),
    ("AGGREGATE", "clk05_ext.latest(timestamptz)", "pg_proc", "to_regprocedure"),
)
_RELEASE = ("DO $release$BEGIN IF EXISTS (SELECT 1 FROM pg_catalog.pg_depend WHERE deptype = 'e' AND classid = "
            "'pg_catalog.%s'::pg_catalog.regclass AND objid = pg_catalog.%s('%s')) THEN ALTER EXTENSION plpgsql DROP %s %s; "
            "END IF; END$release$")
NEW_PLACES = ("domain default", "policy", "rule", "aggregate initial value", "publication filter", "timetz use")
PLACES = {
    ("domain default", "clk05_places.day"): ["clock word in a string: 'today'"],
    ("policy", "clk05_places.visible cut"): ["clock word in a string: 'today'", "clock word in a string: 'yesterday'"],
    ("rule", "clk05_places.log keep"): ["now()"],
    ("aggregate initial value", "clk05_places.latest(timestamp with time zone)"): ["clock word in a string: 'now'"],
    ("aggregate initial value", "clk05_places.joined(text)"): ["clock word in a string: 'yesterday'"],
    ("publication filter", "clk05_pub clk05_places.log"): ["clock word in a string: 'tomorrow'"],
    ("timetz use", "column clk05_places.shift.starts"): ["timetz"],
    ("timetz use", "routine clk05_places.opening()"): ["timetz"],
    ("timetz use", "type clk05_places.wall"): ["timetz"],
    ("timetz use", "type clk05_places._wall"): ["timetz"],
}

# The allow-list (v2.1 section 3): the only way a finding is cleared. Each entry: the object's kind and name, the
# exact finding, and the reason. An entry is added only with the Human Governor's approval; an entry that matches
# nothing fails the check.
_S015_DIAGNOSTIC = ("clock word in a string: 'S015 cache coordinate diagnostic: anchor=%s id=%s state_at=%s known_at=%s "
                    "now=%s statement_start=%s xact_start=%s'")
ALLOWED = {
    ("function", "public.s015_compute_governed_cache()", _S015_DIAGNOSTIC):
        "A diagnostic message's format template; the word labels a value, and the server rejects the string as a time",
    ("function", "public.s015_refresh_governed_cache(text, bigint, timestamp with time zone, timestamp with time zone)",
     _S015_DIAGNOSTIC): "The same diagnostic message",
    # Approved by the Human Governor, 14 Nov 2026 (CK-2R10, option A): two error messages read once RAISE messages are
    # read as strings (the Advisor's CK-2R9 final check, finding 2).
    ("function", "public.s015_compute_governed_cache()",
     "clock word in a string: 'S015 cache coordinates must satisfy state_at <= known_at <= now'"):
        "An error message's text: the word names the coordinate the message checks; the message reads no clock",
    ("function", "public.s015_refresh_governed_cache(text, bigint, timestamp with time zone, timestamp with time zone)",
     "clock word in a string: 'S015 refresh: coordinates must satisfy state_at <= known_at <= now'"):
        "The same kind of error message, in the refresh",
}


CLOCK_READS = frozenset(name + "()" for name in CALLED_BY_NAME) | frozenset(KEYWORDS.values())


def _excused(key, reading):
    """A face may read its own built-in, and the moving part may read the clock (F1, F2), and nothing else is excused
    by being a face or the moving part: any other finding there is reported as anywhere (rule v2.1 sections 1, 3)."""
    kind, name = key
    if kind != "function":
        return False
    if name in FACES:
        return reading == FACES[name]
    return name in MOVING_PART and reading in CLOCK_READS


def catalogue_problems(seen, readings, allowed=None):
    """Return what is wrong with a catalogue's readings, by the rule CLK-05 holds the catalogue to: every required kind
    was seen; no text is unreadable, whatever object holds it; each face reads its own built-in; nothing but the
    faces and the moving part reads the time, but for the findings the allow-list clears; and every allow-list entry
    clears a finding."""
    allowed = allowed or {}
    problems = {}
    empty = [kind for kind in REQUIRED_KINDS if not seen.get(kind)]
    if empty:
        problems["kinds the catalogue held none of"] = empty
    unreadable = {"%s %s" % key: found for key, found in readings.items()
                  if any(reading.startswith("unreadable") for reading in found)}
    if unreadable:
        problems["texts that could not be read"] = unreadable
    silent = [face for face, built_in in FACES.items() if built_in not in readings.get(("function", face), [])]
    if silent:
        problems["faces not seen reading their own built-in"] = silent
    elsewhere = {}
    for key, found in readings.items():
        left = [reading for reading in found if key + (reading,) not in allowed and not _excused(key, reading)
                and not reading.startswith("unreadable")]   # an unreadable text is reported under its own heading
        if left:
            elsewhere["%s %s" % key] = left
    if elsewhere:
        problems["time read outside the faces"] = elsewhere
    stale = ["%s %s: %s" % entry for entry in allowed if entry[2] not in readings.get(entry[:2], [])]
    if stale:
        problems["allow-list entries that matched nothing"] = stale
    return problems


_ALL_KINDS = {kind: 1 for kind in REQUIRED_KINDS}
_FACES_READ = {("function", face): [built_in] for face, built_in in FACES.items()}
# The rule tried on constructed catalogues. Each line: what was seen, the readings, the allow-list, and the problems'
# headings.
_LABEL = ("view", "public.v", "clock word in a string: 'today'")
RULE = (
    (_ALL_KINDS, _FACES_READ, {}, []),
    (_ALL_KINDS, {**_FACES_READ, ("function", "public.intevia_test_clock_shift()"): ["clock_timestamp()"]}, {}, []),
    # The Advisor's CK-2R3 case: a face that cannot be read is not excused, and does not count as reading.
    (_ALL_KINDS, {**_FACES_READ, ("function", "public.intevia_clock_statement()"):
                  ["unreadable as a routine in internal: 'a routine in internal'"]}, {},
     ["faces not seen reading their own built-in", "texts that could not be read"]),
    (_ALL_KINDS, {**_FACES_READ, ("function", "public.intevia_test_clock_shift()"): ["unreadable as plpgsql: x"]}, {},
     ["texts that could not be read"]),
    (_ALL_KINDS, {**_FACES_READ, ("function", "public.intevia_clock_instant()"): ["statement_timestamp()"]}, {},
     ["faces not seen reading their own built-in", "time read outside the faces"]),
    (_ALL_KINDS, {**_FACES_READ, ("view", "public.v"): ["now()"]}, {}, ["time read outside the faces"]),
    ({**_ALL_KINDS, "view": 0}, _FACES_READ, {}, ["kinds the catalogue held none of"]),
    # A new place may be empty; a required kind may not.
    ({**_ALL_KINDS, "policy": 0}, _FACES_READ, {}, []),
    # The allow-list clears exactly its entry, and an entry that clears nothing fails the check.
    (_ALL_KINDS, {**_FACES_READ, ("view", "public.v"): [_LABEL[2]]}, {_LABEL: "a label"}, []),
    (_ALL_KINDS, {**_FACES_READ, ("view", "public.v"): [_LABEL[2], "now()"]}, {_LABEL: "a label"}, ["time read outside the faces"]),
    (_ALL_KINDS, _FACES_READ, {_LABEL: "a label"}, ["allow-list entries that matched nothing"]),
    # A face is excused only for its own built-in, and the moving part only for reading the clock (F1, F2): any other
    # finding there is reported (the Advisor's CK-2R9 final check, finding 1).
    (_ALL_KINDS, {**_FACES_READ, ("function", "public.intevia_clock_instant()"):
                  ["clock word in a string: 'today'", "clock_timestamp()"]}, {}, ["time read outside the faces"]),
    (_ALL_KINDS, {**_FACES_READ, ("function", "public.intevia_clock_instant()"): ["clock_timestamp()", "now()"]}, {},
     ["time read outside the faces"]),
    (_ALL_KINDS, {**_FACES_READ, ("function", "public.intevia_test_clock_shift()"): ["clock_timestamp()", "current_date"]},
     {}, []),
    (_ALL_KINDS, {**_FACES_READ, ("function", "public.intevia_test_clock_shift()"):
                  ["clock_timestamp()", "clock word in a string: 'now'"]}, {}, ["time read outside the faces"]),
    (_ALL_KINDS, {**_FACES_READ, ("function", "public.intevia_test_clock_shift()"): ["timetz"]}, {},
     ["time read outside the faces"]),
)


class OnlyOneClockTests(TransactionTestCase):
    def test_the_catalogue_reads_the_time_only_through_the_faces(self):
        """CLK-05, catalogue half. No SQL the database holds reads the time as written (a clock function, a time
        keyword, a clock word in a string, one-argument age, any use of timetz) but the three faces, in an owned test
        database the routines that move them, and the entries the Human Governor allows."""
        for form, text, expected in SPELLINGS + BESIDE_A_CALL + ADVISOR_CK2R9 + ADVISOR_CK2R10 + GEMINI_CK2R10:
            self.assertEqual(time_readings(form, text, TableTypes(TABLE_ROW_TYPES)), expected,
                             "CLK-05: PostgreSQL's parser must find every time read written in a text and nothing else; "
                             "this text, read as %s, was read wrongly: %r" % (form, text))
        # The reading's two cross-checks, each shown failing on purpose with a wrong literal reading put in its place: a
        # parse-tree constant that differs from the text's literal, and a RAISE message the body's literals do not hold,
        # each make the text unreadable.
        module = sys.modules[__name__]
        with patch.object(module, "_string_literals", lambda text: [(7, "plain", "b")]):
            self.assertIn("unreadable as sql: the string constant at byte 7 reads differently", time_readings("sql", "SELECT 'a'"),
                          "CLK-05: a constant the parser holds must agree with the literal read from the text.")
        raised = plpgsql("BEGIN RAISE NOTICE 'today'; END")
        with patch.object(module, "_string_literals", lambda text: [(0, "dollar", "BEGIN RAISE NOTICE 'today'; END")]
                          if text == raised else []):
            self.assertIn("unreadable as plpgsql: a RAISE message was not found as a string constant in the body",
                          time_readings("plpgsql", raised), "CLK-05: every RAISE message must be among the body's literals.")
        for texts, expected in FIELDS:
            self.assertEqual(readings_of(routine_texts(*texts), TableTypes(TABLE_ROW_TYPES)), expected,
                             "CLK-05: each text of a routine must be read on its own. These were read wrongly: %r" % (texts,))
        for seen, readings, allowed, expected in RULE:
            self.assertEqual(sorted(catalogue_problems(seen, readings, allowed)), sorted(expected),
                             "CLK-05: the rule the catalogue is held to judged this constructed catalogue wrongly: %r" % (readings,))
        with connection.cursor() as cursor:
            seen, readings = catalogue_readings(cursor)
        self.assertEqual(catalogue_problems(seen, readings, ALLOWED), {},
                         "CLK-05: only the INTEVIA Clock's faces may read PostgreSQL's time, each its own built-in, and "
                         "every text the catalogue holds must be readable. Send each time read through "
                         "public.intevia_clock_statement(), _instant() or _transaction(), reword a clock word, and make "
                         "each text readable; only the Human Governor adds an allow-list entry.")
        # Names resolve as the catalogue resolves them for the routine (F6 through domains, and the row-type retry).
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_catalog.current_setting('search_path')")
            before = cursor.fetchone()[0]
            try:
                for statement in TYPE_IDENTITY_SETUP:
                    cursor.execute(statement)
                types = CatalogueTypes(cursor)
                for texts, expected in TYPE_IDENTITY:
                    self.assertEqual(readings_of(texts, types), expected,
                                     "CLK-05: a type must be the one the catalogue resolves for the routine, not the one "
                                     "its spelling suggests. These were read wrongly: %r" % (texts,))
                # Installed routines, each read as the catalogue scan reads it.
                for statement in ROUTINE_IDENTITY_SETUP:
                    cursor.execute(statement)
                for name, created, expected in ROUTINE_IDENTITY:
                    cursor.execute(created)
                    cursor.execute("SELECT '%s.%s()'::regprocedure::oid" % ("clk05_owner" if name == "definer" else "clk05_types", name))
                    cursor.execute(ROUTINE_ROW.replace("{oid}", str(int(cursor.fetchone()[0]))))
                    row = cursor.fetchone()
                    if name == "definer":
                        self.assertEqual(function_readings(row, CatalogueTypes(cursor)), [],
                                         "CLK-05: with the reading role as its owner, this routine's $user names no "
                                         "schema holding the domain: %s" % created)
                        row = row[:8] + ("clk05_owner",) + row[9:]
                    self.assertEqual(function_readings(row, CatalogueTypes(cursor)), expected,
                                     "CLK-05: a routine's names resolve under its own search path, a definer's $user is "
                                     "its owner, and x%%ROWTYPE is the row of the relation x. This routine was read "
                                     "wrongly: %s" % created)
                # Each new place is read, by an object of its own.
                for statement in PLACES_SETUP:
                    cursor.execute(statement)
                seen, readings = catalogue_readings(cursor, NEW_PLACES)
                self.assertEqual({key: found for key, found in readings.items() if "clk05_places" in key[1]}, PLACES,
                                 "CLK-05: every place the rule names must be read.")
                # An extension's members are read nowhere; the same objects outside it are (PLACES, above).
                for statement in EXTENSION_SETUP:
                    cursor.execute(statement)
                for kind, name, _catalog, _resolver in EXTENSION_MEMBERS:
                    cursor.execute("ALTER EXTENSION plpgsql ADD %s %s" % (kind, name))
                seen, readings = catalogue_readings(cursor)
                self.assertEqual(sorted(key for key in readings if "clk05_ext" in key[1]), [],
                                 "CLK-05: an object that is a member of an extension must not be read, in any place.")
                # Resolving names never changes the reading session's search path, nor a transaction's own.
                with transaction.atomic():
                    cursor.execute("SET LOCAL search_path TO pg_catalog, public")
                    CatalogueTypes(cursor).is_timetz(["text"], ["clk05_types"])
                    CatalogueTypes(cursor).is_row_type("item%ROWTYPE", ["clk05_rows"])
                    cursor.execute("SELECT pg_catalog.current_setting('search_path')")
                    inside = cursor.fetchone()[0]
                cursor.execute("SELECT pg_catalog.current_setting('search_path')")
                self.assertEqual((inside, cursor.fetchone()[0]), ("pg_catalog, public", before),
                                 "CLK-05: resolving a name must leave the session's and the transaction's search path "
                                 "as they were.")
            finally:
                cursor.execute("RESET check_function_bodies")
                for kind, name, catalog, resolver in EXTENSION_MEMBERS:
                    cursor.execute(_RELEASE % (catalog, resolver, name, kind, name))
                cursor.execute("DROP PUBLICATION IF EXISTS clk05_pub")
                cursor.execute("DROP PUBLICATION IF EXISTS clk05_extpub")
                cursor.execute("DROP SCHEMA IF EXISTS clk05_types, clk05_row, clk05_first, clk05_second, clk05_idx, "
                               "clk05_rows, clk05_owner, clk05_places, clk05_ext CASCADE")
