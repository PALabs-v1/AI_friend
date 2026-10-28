"""Structural index: symbols and relationship edges for the in-scope source files.

Parsers, chosen for what the repository actually contains (548 Python files, 8
Rust files, 3 TS files, a few SQL files):

* Python: the standard library `ast`. Imports, calls, decorators, NATS subjects,
  env reads, SQL table access and dynamic constructs come from the syntax tree.
* Rust and JS/TS: a lexical pass (comments and strings blanked, braces counted).
  No rust-analyzer or tree-sitter: neither is installed, and a new toolchain for
  eight Rust files is the kind of infrastructure the directive warns against.
* SQL: CREATE TABLE statements by regex.

Every edge carries a confidence tier so an inferred edge is never shown as proven:

  proven      resolved through the syntax tree to a symbol that exists
  lexical     resolved by the lexical pass to a unique target (Rust, JS/TS)
  inferred    matched by name only; may be wrong
  external    target is outside the repository (stdlib, third party)
  unresolved  target could not be determined (dynamic code, ambiguous name)

"No static reference found" never means "unused": see `unreferenced`, which
reports that caveat with the dynamic constructs it could not see through.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files (id),
    sha256 TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualname TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    decorators TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS symbols_name ON symbols (name);
CREATE INDEX IF NOT EXISTS symbols_file ON symbols (file_id);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files (id),
    sha256 TEXT NOT NULL,
    kind TEXT NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    dst_file_id INTEGER,
    line INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    evidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS edges_kind ON edges (kind, dst);
CREATE INDEX IF NOT EXISTS edges_file ON edges (file_id);
CREATE INDEX IF NOT EXISTS edges_dst_file ON edges (dst_file_id);
"""

CODE_LANGS = {"python", "rust", "javascript", "typescript", "sql"}
DOC_LANGS = {
    "markdown",
    "json",
    "jsonl",
    "toml",
    "yaml",
    "shell",
    "powershell",
    "batch",
    "jupyter",
    "text",
}
PARSED_LANGS = CODE_LANGS | DOC_LANGS
PARSER_USED = {
    "python": "python-ast",
    "rust": "rust-lexical",
    "javascript": "js-lexical",
    "typescript": "js-lexical",
    "sql": "sql-lexical",
    "markdown": "markdown-headings+links",
    "json": "json",
    "jsonl": "jsonl",
    "toml": "toml",
    "yaml": "yaml-lexical",
    "shell": "shell-lexical",
    "powershell": "line-count",
    "batch": "line-count",
    "jupyter": "json",
    "text": "line-count",
}
SUBJECT_RE = re.compile(r"^[A-Za-z0-9_*>\-]+(\.[A-Za-z0-9_*>\-{}]+)+$")
SQL_WRITE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][\w]*)",
    re.IGNORECASE,
)
SQL_TABLE = re.compile(
    r"CREATE\s+(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w.]*)",
    re.IGNORECASE,
)
SQL_READ = re.compile(r"\bFROM\s+([A-Za-z_][\w]*)", re.IGNORECASE)
HTTP_VERBS = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "websocket",
    "route",
    "head",
    "options",
}
BUS_VERBS = {"publish", "subscribe", "request", "js_publish", "publish_json"}
COMMON_METHODS = {
    "get",
    "set",
    "add",
    "run",
    "start",
    "stop",
    "close",
    "open",
    "read",
    "write",
    "update",
    "append",
    "pop",
    "keys",
    "values",
    "items",
    "copy",
    "clear",
    "send",
    "join",
    "split",
    "strip",
    "format",
    "lower",
    "upper",
    "load",
    "dump",
    "loads",
    "dumps",
    "info",
    "debug",
    "warning",
    "error",
    "exception",
    "encode",
    "decode",
    "reset",
    "put",
    "wait",
    "done",
}
STDLIB = set(getattr(sys, "stdlib_module_names", ()))


@dataclass
class Sym:
    kind: str
    name: str
    qualname: str
    line: int
    end_line: int
    decorators: str = ""


@dataclass
class Edge:
    kind: str
    src: str
    dst: str
    line: int
    confidence: str
    evidence: str = ""
    dst_path: str | None = None


@dataclass
class Extract:
    path: str
    language: str
    symbols: list[Sym] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    consts: dict[str, str] = field(
        default_factory=dict
    )  # name or Class.NAME -> str value
    raw_calls: list[dict] = field(default_factory=list)  # resolved in pass 2
    raw_imports: list[dict] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------- Python


def _dotted_keys(path: str) -> list[str]:
    p = path.removesuffix(".py")
    parts = p.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    keys = [".".join(parts)]
    if parts and parts[0] == "backend" and len(parts) > 1:
        keys.append(".".join(parts[1:]))
    return [k for k in keys if k]


def _package_of(path: str) -> list[str]:
    parts = path[:-3].split("/")
    return parts[:-1]


def _first_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _PyVisitor(ast.NodeVisitor):
    def __init__(self, path: str, src: str, out: Extract):
        self.path, self.out, self.src = path, out, src
        self.scope: list[tuple[str, str]] = []  # (kind, name)
        self.bindings: dict[str, dict] = {}
        self.local_defs: set[str] = set()

    # --- helpers
    def qual(self) -> str:
        return ".".join(n for _, n in self.scope) or "<module>"

    def cls(self) -> str | None:
        for kind, name in reversed(self.scope):
            if kind == "class":
                return name
            if kind == "function":
                continue
        return None

    def edge(self, kind, dst, node, conf, ev="", dst_path=None):
        self.out.edges.append(
            Edge(
                kind,
                self.qual(),
                dst,
                getattr(node, "lineno", 0),
                conf,
                ev[:200],
                dst_path,
            )
        )

    # --- definitions
    def _def(self, node, kind):
        decs = [ast.unparse(d)[:80] for d in node.decorator_list]
        parent_is_class = bool(self.scope) and self.scope[-1][0] == "class"
        k = "method" if (kind == "function" and parent_is_class) else kind
        q = ".".join([n for _, n in self.scope] + [node.name])
        self.out.symbols.append(
            Sym(
                k,
                node.name,
                q,
                node.lineno,
                getattr(node, "end_lineno", node.lineno),
                "; ".join(decs),
            )
        )
        if not self.scope:
            self.local_defs.add(node.name)
        self.scope.append((kind, node.name))
        for d in node.decorator_list:
            self.visit_decorator(d, node)
        if kind == "class":
            for b in node.bases:
                self.edge(
                    "INHERITS",
                    ast.unparse(b)[:80],
                    b,
                    "inferred",
                    f"class {node.name}({ast.unparse(b)[:60]})",
                )
        self.generic_visit(node)
        self.scope.pop()

    def visit_decorator(self, d, owner):
        call = d if isinstance(d, ast.Call) else None
        target = call.func if call else d
        text = ast.unparse(target)[:80]
        self.out.edges.append(
            Edge(
                "DECORATED_BY",
                ".".join([n for _, n in self.scope]),
                text,
                owner.lineno,
                "proven",
                text,
            )
        )
        if (
            isinstance(target, ast.Attribute)
            and target.attr in HTTP_VERBS
            and call
            and call.args
        ):
            route = _first_str(call.args[0])
            if route is not None:
                self.out.edges.append(
                    Edge(
                        "REGISTERS_ROUTE",
                        ".".join([n for _, n in self.scope]),
                        f"{target.attr.upper()} {route}",
                        owner.lineno,
                        "proven",
                        text,
                    )
                )

    def visit_FunctionDef(self, node):
        self._def(node, "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._def(node, "class")

    # --- constants (module level and class level strings)
    def visit_Assign(self, node):
        val = _first_str(node.value)
        if (
            val is not None
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name = node.targets[0].id
            if self.scope and self.scope[-1][0] == "class":
                self.out.consts[f"{self.scope[-1][1]}.{name}"] = val
            elif not self.scope:
                self.out.consts[name] = val
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        val = _first_str(node.value) if node.value is not None else None
        if val is not None and isinstance(node.target, ast.Name):
            name = node.target.id
            if self.scope and self.scope[-1][0] == "class":
                self.out.consts[f"{self.scope[-1][1]}.{name}"] = val
            elif not self.scope:
                self.out.consts[name] = val
        self.generic_visit(node)

    # --- imports
    def visit_Import(self, node):
        for a in node.names:
            bind = a.asname or a.name.split(".")[0]
            mod = a.name if a.asname else a.name.split(".")[0]
            self.bindings[bind] = {"module": a.name if a.asname else mod, "name": None}
            self.out.raw_imports.append(
                {
                    "module": a.name,
                    "name": None,
                    "level": 0,
                    "line": node.lineno,
                    "src": self.qual(),
                }
            )

    def visit_ImportFrom(self, node):
        mod = node.module or ""
        for a in node.names:
            if a.name == "*":
                self.out.raw_imports.append(
                    {
                        "module": mod,
                        "name": "*",
                        "level": node.level,
                        "line": node.lineno,
                        "src": self.qual(),
                    }
                )
                continue
            self.bindings[a.asname or a.name] = {
                "module": mod,
                "name": a.name,
                "level": node.level,
            }
            self.out.raw_imports.append(
                {
                    "module": mod,
                    "name": a.name,
                    "level": node.level,
                    "line": node.lineno,
                    "src": self.qual(),
                    "as": a.asname or a.name,
                }
            )

    # --- strings: SQL
    def visit_Constant(self, node):
        if (
            isinstance(node.value, str)
            and len(node.value) > 12
            and not self.src_is_docstring(node)
        ):
            s = node.value
            for m in SQL_TABLE.finditer(s):
                self.out.symbols.append(
                    Sym(
                        "table",
                        m.group(1).lower(),
                        m.group(1).lower(),
                        node.lineno,
                        node.lineno,
                    )
                )
            for m in SQL_WRITE.finditer(s):
                self.edge(
                    "WRITES_STORAGE",
                    f"table:{m.group(1).lower()}",
                    node,
                    "inferred",
                    s[:80].replace("\n", " "),
                )
            if re.search(r"\bSELECT\b", s, re.IGNORECASE):
                for m in SQL_READ.finditer(s):
                    self.edge(
                        "READS_STORAGE",
                        f"table:{m.group(1).lower()}",
                        node,
                        "inferred",
                        s[:80].replace("\n", " "),
                    )

    @staticmethod
    def src_is_docstring(_node) -> bool:
        return False

    # --- calls
    def visit_Call(self, node):
        f = node.func
        # NATS bus verbs
        fname = (
            f.attr
            if isinstance(f, ast.Attribute)
            else (f.id if isinstance(f, ast.Name) else "")
        )
        if isinstance(f, ast.Attribute) and f.attr in BUS_VERBS:
            arg = node.args[0] if node.args else None
            if arg is None:
                for kw in node.keywords:
                    if kw.arg == "subject":
                        arg = kw.value
            self._bus(f.attr, arg, node)
        elif fname and ("publish" in fname or "subscribe" in fname) and node.args:
            # helper or callback (publish_cb, _publish, subscribe_all): only a readable subject counts
            self._bus(fname, node.args[0], node, quiet=True)
        # env reads
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "getenv"
            or (isinstance(f, ast.Name) and f.id == "getenv")
        ):
            v = _first_str(node.args[0]) if node.args else None
            if v:
                self.edge("READS_ENV", v, node, "proven", f"getenv({v!r})")
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "get"
            and ast.unparse(f.value).endswith("environ")
            and node.args
        ):
            v = _first_str(node.args[0])
            if v:
                self.edge("READS_ENV", v, node, "proven", f"environ.get({v!r})")
        # dynamic constructs
        name = ast.unparse(f)[:60]
        if name in ("importlib.import_module", "import_module", "__import__"):
            self.edge(
                "DYNAMIC", "dynamic import", node, "unresolved", ast.unparse(node)[:100]
            )
        elif name in ("eval", "exec", "globals", "locals"):
            self.edge("DYNAMIC", name, node, "unresolved", ast.unparse(node)[:100])
        elif (
            name == "getattr"
            and len(node.args) >= 2
            and _first_str(node.args[1]) is None
        ):
            self.edge(
                "DYNAMIC",
                "getattr with a computed name",
                node,
                "unresolved",
                ast.unparse(node)[:100],
            )
        elif name.endswith(("entry_points", "iter_entry_points")):
            self.edge(
                "DYNAMIC",
                "plugin discovery",
                node,
                "unresolved",
                ast.unparse(node)[:100],
            )
        # task registration
        if (
            isinstance(f, ast.Attribute)
            and f.attr
            in ("create_task", "ensure_future", "spawn", "run_in_executor", "to_thread")
            or name in ("asyncio.create_task", "asyncio.to_thread")
        ):
            inner = next((a for a in node.args if isinstance(a, ast.Call)), None)
            if inner is not None:
                self.out.raw_calls.append(
                    {
                        "src": self.qual(),
                        "cls": self.cls(),
                        "func": inner.func,
                        "line": node.lineno,
                        "kind": "STARTS_TASK",
                    }
                )
        self.out.raw_calls.append(
            {
                "src": self.qual(),
                "cls": self.cls(),
                "func": f,
                "line": node.lineno,
                "kind": "CALLS",
            }
        )
        self.generic_visit(node)

    def _bus(self, verb, arg, node, quiet=False):
        kind = "SUBSCRIBES" if "subscribe" in verb else "PUBLISHES"
        lit = _first_str(arg)
        if quiet and lit is not None and not SUBJECT_RE.match(lit):
            return
        if lit is not None:
            self.edge(kind, lit, node, "proven", f"{verb}({lit!r})")
            return
        if isinstance(arg, (ast.Attribute, ast.Name)):
            self.out.raw_calls.append(
                {
                    "src": self.qual(),
                    "cls": self.cls(),
                    "bus": kind,
                    "ref": ast.unparse(arg),
                    "line": node.lineno,
                    "quiet": quiet,
                }
            )
            return
        if quiet:
            return
        if isinstance(arg, ast.JoinedStr):
            pat = "".join(
                v.value if isinstance(v, ast.Constant) else "{}" for v in arg.values
            )
            self.edge(kind, pat, node, "inferred", f"{verb}(f-string)")
            return
        self.edge(
            kind,
            "<dynamic>",
            node,
            "unresolved",
            f"{verb}({ast.unparse(arg)[:50] if arg is not None else ''})",
        )

    def visit_Subscript(self, node):
        if ast.unparse(node.value).endswith("environ"):
            v = _first_str(node.slice)
            if v:
                self.edge("READS_ENV", v, node, "proven", f"environ[{v!r}]")
        self.generic_visit(node)

    def visit_If(self, node):
        t = node.test
        if isinstance(t, ast.Compare) and ast.unparse(t.left) == "__name__":
            self.out.symbols.append(
                Sym(
                    "entrypoint",
                    "__main__",
                    "<module>.__main__",
                    node.lineno,
                    getattr(node, "end_lineno", node.lineno),
                )
            )
        self.generic_visit(node)


def extract_python(path: str, text: str) -> Extract:
    out = Extract(path, "python")
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as exc:
        out.error = f"SyntaxError line {exc.lineno}: {exc.msg}"
        return out
    v = _PyVisitor(path, text, out)
    v.visit(tree)
    out._bindings = v.bindings  # type: ignore[attr-defined]
    out._local_defs = v.local_defs  # type: ignore[attr-defined]
    return out


# ---------------------------------------------------------------- lexical (Rust, JS/TS, SQL)


def _blank(text: str, rust: bool) -> str:
    """Blank comments and string bodies, keeping length and newlines, so brace counts hold."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        two = text[i : i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(
                '"'
                + "".join("\n" if ch == "\n" else " " for ch in text[i + 1 : j - 1])
                + '"'
            )
            i = j
        elif c == "`" and not rust:
            j = text.find("`", i + 1)
            j = n if j < 0 else j + 1
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


RS_ITEM = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|const\s+)*"
    r"(fn|struct|enum|trait|mod|type|const|static)\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)
RS_IMPL = re.compile(
    r"^\s*impl(?:<[^>]*>)?\s+(?:([\w:]+)(?:<[^>]*>)?\s+for\s+)?([\w:]+)", re.MULTILINE
)
RS_USE = re.compile(r"^\s*(?:pub\s+)?use\s+([^;]+);", re.MULTILINE)
RS_MOD = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;", re.MULTILINE
)
RS_BUS = re.compile(
    r"\.\s*(publish\w*|subscribe\w*|request\w*)\s*\(\s*([\w:\"\.\-\*>{}]+)", re.DOTALL
)
RS_CONST = re.compile(
    r'(?:pub\s+)?const\s+([A-Z][A-Z0-9_]*)\s*:\s*&(?:\'static\s+)?str\s*=\s*"([^"]*)"'
)
RS_CALL = re.compile(r"(?<![\w.])(?:[\w]+::)*([a-z_]\w*)\s*\(")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _brace_end(blank: str, start: int) -> int:
    """Index just past the matching '}' of the first '{' at or after start; len if none."""
    i = blank.find("{", start)
    if i < 0:
        return len(blank)
    depth = 0
    for j in range(i, len(blank)):
        if blank[j] == "{":
            depth += 1
        elif blank[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(blank)


def extract_rust(path: str, text: str) -> Extract:
    out = Extract(path, "rust")
    blank = _blank(text, rust=True)
    impl_spans: list[tuple[int, int, str]] = []
    for m in RS_IMPL.finditer(blank):
        end = _brace_end(blank, m.end())
        target = m.group(2).split("::")[-1]
        impl_spans.append((m.start(), end, target))
        out.symbols.append(
            Sym(
                "impl",
                target if not m.group(1) else f"{target}:{m.group(1)}",
                f"impl {m.group(1) + ' for ' if m.group(1) else ''}{target}",
                _line_of(text, m.start()),
                _line_of(text, end),
            )
        )
        if m.group(1):
            out.edges.append(
                Edge(
                    "IMPLEMENTS",
                    target,
                    m.group(1),
                    _line_of(text, m.start()),
                    "lexical",
                    m.group(0).strip()[:100],
                )
            )
    fn_spans: list[tuple[int, int, str]] = []
    for m in RS_ITEM.finditer(blank):
        kind, name = m.group(1), m.group(2)
        if kind in ("fn", "struct", "enum", "trait", "mod") or kind in (
            "type",
            "const",
            "static",
        ):
            end = (
                _brace_end(blank, m.end())
                if kind in ("fn", "struct", "enum", "trait", "mod")
                else m.end()
            )
            if kind in ("struct", "mod") and blank[
                m.end() : m.end() + 80
            ].lstrip().startswith((";", "(")):
                end = m.end()
            owner = next((t for s, e, t in impl_spans if s < m.start() < e), None)
            q = f"{owner}::{name}" if owner else name
            k = "method" if (kind == "fn" and owner) else kind
            out.symbols.append(
                Sym(k, name, q, _line_of(text, m.start()), _line_of(text, end))
            )
            if kind == "fn":
                fn_spans.append((m.start(), end, q))
    for m in RS_CONST.finditer(text):
        out.consts[m.group(1)] = m.group(2)
    for m in RS_USE.finditer(blank):
        out.raw_imports.append(
            {"use": m.group(1).strip(), "line": _line_of(text, m.start())}
        )
    for m in RS_MOD.finditer(blank):
        out.raw_imports.append({"mod": m.group(1), "line": _line_of(text, m.start())})
    names_here = {s.name for s in out.symbols if s.kind in ("fn", "method")}
    for s, e, q in fn_spans:
        body = blank[s:e]
        for m in RS_CALL.finditer(body):
            callee = m.group(1)
            if (
                callee in ("if", "while", "for", "match", "fn", "return", "loop")
                or callee == q.split("::")[-1]
            ):
                continue
            out.raw_calls.append(
                {
                    "src": q,
                    "name": callee,
                    "line": _line_of(text, s + m.start()),
                    "local": callee in names_here,
                }
            )
    for m in RS_BUS.finditer(text):
        verb, arg = m.group(1), m.group(2)
        owner = next((q for s, e, q in fn_spans if s <= m.start() < e), "<module>")
        kind = "SUBSCRIBES" if verb.startswith("subscribe") else "PUBLISHES"
        if arg.startswith('"'):
            out.edges.append(
                Edge(
                    kind,
                    owner,
                    arg.strip('"'),
                    _line_of(text, m.start()),
                    "lexical",
                    f"{verb}({arg}",
                )
            )
        else:
            out.raw_calls.append(
                {
                    "src": owner,
                    "bus": kind,
                    "ref": arg,
                    "line": _line_of(text, m.start()),
                }
            )
    return out


JS_IMPORT = re.compile(
    r"""(?:import\s+(?:[^'"]*?\s+from\s+)?|export\s+[^'"]*?\s+from\s+|require\(\s*)['"]([^'"]+)['"]"""
)
JS_DYN = re.compile(r"""import\(\s*([^)]+)\)""")
JS_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(function\*?|class|interface|type|enum|const|let)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


def extract_js(path: str, text: str, lang: str) -> Extract:
    out = Extract(path, lang)
    blank = _blank(text, rust=False)
    for m in JS_DEF.finditer(blank):
        kind = {
            "function": "function",
            "function*": "function",
            "class": "class",
            "interface": "interface",
            "type": "type",
            "enum": "enum",
            "const": "const",
            "let": "const",
        }[m.group(1)]
        line = _line_of(text, m.start())
        if kind == "const":
            nxt = blank[m.end() : m.end() + 160]
            if "=>" not in nxt.split("\n")[0] and "function" not in nxt.split("\n")[0]:
                continue
            kind = "function"
        out.symbols.append(Sym(kind, m.group(2), m.group(2), line, line))
    for m in JS_IMPORT.finditer(text):
        out.raw_imports.append({"spec": m.group(1), "line": _line_of(text, m.start())})
    for m in JS_DYN.finditer(blank):
        arg = text[m.start() : m.end()]
        spec = re.search(r"""['"]([^'"]+)['"]""", arg)
        if spec:
            out.raw_imports.append(
                {
                    "spec": spec.group(1),
                    "line": _line_of(text, m.start()),
                    "dynamic": True,
                }
            )
        else:
            out.edges.append(
                Edge(
                    "DYNAMIC",
                    "<module>",
                    "dynamic import",
                    _line_of(text, m.start()),
                    "unresolved",
                    arg[:100],
                )
            )
    return out


def extract_sql(path: str, text: str) -> Extract:
    out = Extract(path, "sql")
    for m in SQL_TABLE.finditer(text):
        ln = _line_of(text, m.start())
        out.symbols.append(Sym("table", m.group(1).lower(), m.group(1).lower(), ln, ln))
    return out


MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
MD_PATH = re.compile(
    r"`([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-*]+)+\.(?:py|rs|md|toml|ya?ml|json|sh|ts|tsx|js|sql))(?::\d+(?:-\d+)?)?`"
)
YAML_KEY = re.compile(r"^([A-Za-z_][\w.\-]*):(?:\s|$)")
SH_FUNC = re.compile(
    r"^\s*(?:function\s+)?([A-Za-z_][\w\-]*)\s*\(\)\s*\{?", re.MULTILINE
)


def extract_other(path: str, text: str, lang: str) -> Extract:
    """Documents and configuration: validate what has a grammar, list what has structure."""
    import tomllib

    out = Extract(path, lang)
    lines = text.split("\n")
    if lang == "markdown":
        fence = False
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith("```"):
                fence = not fence
                continue
            if fence:
                continue
            m = MD_HEADING.match(line)
            if m:
                out.symbols.append(
                    Sym(
                        "heading",
                        m.group(2)[:100],
                        f"h{len(m.group(1))}:{m.group(2)[:100]}",
                        i,
                        i,
                    )
                )
            for lm in MD_LINK.finditer(line):
                tgt = lm.group(1).split("#")[0]
                if (
                    tgt
                    and not re.match(r"^[a-z][a-z0-9+.\-]*:", tgt, re.IGNORECASE)
                    and not tgt.startswith("/")
                ):
                    out.raw_imports.append({"doc_link": tgt, "line": i})
            for pm in MD_PATH.finditer(line):
                out.raw_imports.append({"doc_path": pm.group(1), "line": i})
    elif lang in ("json", "jupyter"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            out.error = f"invalid JSON: {exc}"
            return out
        if isinstance(data, dict):
            for k in list(data)[:60]:
                out.symbols.append(Sym("key", str(k)[:80], str(k)[:80], 1, 1))
    elif lang == "jsonl":
        for i, line in enumerate(lines, 1):
            if line.strip():
                try:
                    json.loads(line)
                except ValueError as exc:
                    out.error = f"invalid JSONL line {i}: {exc}"
                    return out
    elif lang == "toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            out.error = f"invalid TOML: {exc}"
            return out
        for k in list(data)[:60]:
            out.symbols.append(Sym("key", str(k)[:80], str(k)[:80], 1, 1))
    elif lang == "yaml":
        for i, line in enumerate(lines, 1):
            if "\t" in line[: len(line) - len(line.lstrip())]:
                out.error = f"tab indentation on line {i}"
                return out
            m = YAML_KEY.match(line)
            if m:
                out.symbols.append(Sym("key", m.group(1), m.group(1), i, i))
    elif lang == "shell":
        for m in SH_FUNC.finditer(text):
            ln = _line_of(text, m.start())
            out.symbols.append(Sym("function", m.group(1), m.group(1), ln, ln))
    return out


# ---------------------------------------------------------------- resolution


class Resolver:
    def __init__(
        self,
        files: dict[str, Extract],
        file_ids: dict[str, int],
        present: set[str] | None = None,
    ):
        self.files, self.ids = files, file_ids
        self.py_mods: dict[str, str | None] = {}
        for p in files:
            if p.endswith(".py"):
                for k in _dotted_keys(p):
                    self.py_mods[k] = (
                        p if k not in self.py_mods else None
                    )  # None = ambiguous
        self.defs: dict[str, set[str]] = {}  # path -> top-level and qualified names
        self.by_name: dict[str, list[tuple[str, Sym]]] = {}
        for p, ex in files.items():
            self.defs[p] = {s.qualname for s in ex.symbols}
            for s in ex.symbols:
                if s.kind in ("function", "method", "class"):
                    self.by_name.setdefault(s.name, []).append((p, s))
        self.consts: dict[str, list[tuple[str, str]]] = {}
        for p, ex in files.items():
            for k, v in ex.consts.items():
                self.consts.setdefault(k, []).append((p, v))
        self.rust_crates = self._rust_crates()
        self.all_paths = set(present) if present is not None else set(file_ids)

    # python
    def py_module(self, mod: str, level: int, path: str) -> str | None:
        if level:
            base = _package_of(path)
            base = base[: len(base) - (level - 1)] if level > 1 else base
            full = ".".join(base + ([mod] if mod else []))
        else:
            full = mod
        return self.py_mods.get(full)

    def resolve_python(self, ex: Extract) -> None:
        path = ex.path
        bindings = getattr(ex, "_bindings", {})
        local = getattr(ex, "_local_defs", set())
        imported_syms: dict[str, tuple[str, str]] = {}
        imported_mods: dict[str, str] = {}
        # imports -> edges and symbol bindings
        for imp in ex.raw_imports:
            mod, name, level = imp["module"], imp["name"], imp.get("level", 0)
            tgt = self.py_module(mod, level, path)
            sub = None
            if name and name != "*":
                sub = self.py_module((mod + "." if mod else "") + name, level, path)
            if sub:
                ex.edges.append(
                    Edge(
                        "IMPORTS",
                        imp["src"],
                        sub,
                        imp["line"],
                        "proven",
                        f"{'.' * level}{mod} import {name}",
                        sub,
                    )
                )
                imported_mods[imp.get("as") or name] = sub
            elif tgt:
                ex.edges.append(
                    Edge(
                        "IMPORTS",
                        imp["src"],
                        tgt,
                        imp["line"],
                        "proven",
                        f"{'.' * level}{mod} import {name}",
                        tgt,
                    )
                )
                if name and name != "*":
                    imported_syms[imp.get("as") or name] = (tgt, name)
            else:
                top = (mod or "").split(".")[0]
                if level == 0 and top in STDLIB:
                    conf = "external"
                elif (
                    level == 0
                    and any(k.split(".")[0] == top for k in self.py_mods)
                    or level
                ):
                    conf = "unresolved"
                else:
                    conf = "external"
                ex.edges.append(
                    Edge(
                        "IMPORTS",
                        imp["src"],
                        mod or ".",
                        imp["line"],
                        conf,
                        f"{'.' * level}{mod} import {name}",
                    )
                )
        for bname, b in bindings.items():
            if b["name"] is None:
                t = self.py_mods.get(b["module"])
                if t:
                    imported_mods[bname] = t
        for e in ex.edges:
            if e.kind == "INHERITS":
                base = e.dst.split("[")[0].split(".")[-1]
                if base in imported_syms:
                    e.dst_path = imported_syms[base][0]
                    e.dst = base
                    e.confidence = (
                        "proven"
                        if base in self.defs.get(e.dst_path, ())
                        else "inferred"
                    )
        # calls
        seen = set()
        for rc in ex.raw_calls:
            if "bus" in rc:
                self._bus_ref(ex, rc, imported_syms, imported_mods)
                continue
            f, src, cls, line = rc["func"], rc["src"], rc["cls"], rc["line"]
            kind = rc["kind"]
            dst, dst_path, conf, _ev = None, None, "unresolved", ""
            if isinstance(f, ast.Name):
                n = f.id
                if n in imported_syms:
                    tpath, tname = imported_syms[n]
                    if tname in self.defs.get(tpath, ()):
                        dst, dst_path, conf = tname, tpath, "proven"
                    else:
                        dst, dst_path, conf = tname, tpath, "inferred"
                elif n in local:
                    dst, dst_path, conf = n, path, "proven"
                else:
                    continue  # builtin or local variable call: not a repo edge
            elif isinstance(f, ast.Attribute):
                attr = f.attr
                base = f.value
                if isinstance(base, ast.Name) and base.id in ("self", "cls") and cls:
                    q = f"{cls}.{attr}"
                    if q in self.defs[path]:
                        dst, dst_path, conf = q, path, "proven"
                    else:
                        cand = self.by_name.get(attr, [])
                        if len(cand) == 1 and attr not in COMMON_METHODS:
                            dst, dst_path, conf = (
                                cand[0][1].qualname,
                                cand[0][0],
                                "inferred",
                            )
                        else:
                            continue
                elif isinstance(base, ast.Name) and base.id in imported_mods:
                    tpath = imported_mods[base.id]
                    if attr in self.defs.get(tpath, ()):
                        dst, dst_path, conf = attr, tpath, "proven"
                    else:
                        continue
                elif (
                    isinstance(base, ast.Name)
                    and base.id in imported_syms
                    and (
                        f"{imported_syms[base.id][1]}.{attr}"
                        in self.defs.get(imported_syms[base.id][0], ())
                    )
                ):
                    tpath, tname = imported_syms[base.id]
                    dst, dst_path, conf = f"{tname}.{attr}", tpath, "proven"
                else:
                    cand = self.by_name.get(attr, [])
                    if (
                        len(cand) == 1
                        and attr not in COMMON_METHODS
                        and len(attr) >= 6
                        and cand[0][1].kind == "method"
                    ):
                        dst, dst_path, conf = (
                            cand[0][1].qualname,
                            cand[0][0],
                            "inferred",
                        )
                    else:
                        continue
            else:
                continue
            key = (kind, src, dst, dst_path, line)
            if key in seen:
                continue
            seen.add(key)
            ex.edges.append(Edge(kind, src, dst, line, conf, "", dst_path))
        # test association
        if _is_test(path):
            for e in list(ex.edges):
                if (
                    e.kind == "IMPORTS"
                    and e.dst_path
                    and not _is_test(e.dst_path)
                    and e.dst_path.endswith(".py")
                ):
                    ex.edges.append(
                        Edge(
                            "TEST_VALIDATES",
                            path,
                            e.dst_path,
                            e.line,
                            "proven",
                            e.evidence,
                            e.dst_path,
                        )
                    )

    def _bus_ref(self, ex, rc, imported_syms, imported_mods):
        ref, kind, line, src = rc["ref"], rc["bus"], rc["line"], rc["src"]
        ref = ref.removesuffix(".value")
        val = None
        cands = self.consts.get(ref) or self.consts.get(
            ref.split(".", 1)[-1] if "." in ref else ref
        )
        if "." in ref:
            cands = (
                self.consts.get(ref)
                or self.consts.get(
                    ref.rsplit(".", 2)[-2] + "." + ref.rsplit(".", 1)[-1]
                )
                if ref.count(".") >= 1
                else cands
            )
            cands = cands or self.consts.get(ref.split(".")[-1])
        if cands:
            uniq = {v for _, v in cands}
            if len(uniq) == 1:
                val = next(iter(uniq))
        if val and SUBJECT_RE.match(val):
            ex.edges.append(
                Edge(
                    kind,
                    src,
                    val,
                    line,
                    "proven"
                    if ref.count(".") >= 1 and ref in self.consts
                    else "inferred",
                    f"{ref} = {val!r}",
                )
            )
        elif not rc.get("quiet"):
            ex.edges.append(
                Edge(
                    kind,
                    src,
                    f"<{ref}>",
                    line,
                    "unresolved",
                    f"subject expression {ref}",
                )
            )

    # rust
    def _rust_crates(self) -> dict[str, str]:
        crates = {}
        for p in self.files:
            if p.endswith(("/src/lib.rs", "/src/main.rs")):
                root = p.rsplit("/src/", 1)[0]
                crates.setdefault(root.rsplit("/", 1)[-1].replace("-", "_"), root)
        return crates

    def resolve_rust(self, ex: Extract) -> None:
        path = ex.path
        crate_root = path.rsplit("/src/", 1)[0] if "/src/" in path else None
        src_dir = f"{crate_root}/src" if crate_root else None
        here = path.rsplit("/", 1)[0]

        def find(base: str, segs: list[str]) -> str | None:
            for n in range(len(segs), 0, -1):
                sub = "/".join(segs[:n])
                for cand in (f"{base}/{sub}.rs", f"{base}/{sub}/mod.rs"):
                    if cand in self.files:
                        return cand
            return None

        for imp in ex.raw_imports:
            if "mod" in imp:
                base = here
                if not path.endswith(("lib.rs", "main.rs", "mod.rs")):
                    base = f"{here}/{path.rsplit('/', 1)[-1][:-3]}"
                tgt = find(base, [imp["mod"]])
                ex.edges.append(
                    Edge(
                        "IMPORTS",
                        "<module>",
                        tgt or imp["mod"],
                        imp["line"],
                        "lexical" if tgt else "unresolved",
                        f"mod {imp['mod']};",
                        tgt,
                    )
                )
            else:
                use = imp["use"]
                head = re.split(r"::|\s|\{", use, maxsplit=1)[0]
                segs = [
                    s
                    for s in re.split(r"::|[{},\s]+", use)
                    if s and s not in ("self", "super", "crate")
                ][1:]
                tgt = None
                if head == "crate" and src_dir:
                    tgt = find(
                        src_dir, [s for s in re.split(r"::|[{},\s]+", use)[1:] if s]
                    )
                elif head in self.rust_crates:
                    r = self.rust_crates[head]
                    lib = f"{r}/src/lib.rs"
                    tgt = (find(f"{r}/src", segs) if segs else None) or (
                        lib if lib in self.files else None
                    )
                conf = (
                    "lexical"
                    if tgt
                    else ("external" if head not in ("super", "self") else "unresolved")
                )
                ex.edges.append(
                    Edge(
                        "IMPORTS",
                        "<module>",
                        tgt or use[:80],
                        imp["line"],
                        conf,
                        f"use {use[:80]}",
                        tgt,
                    )
                )
        for rc in ex.raw_calls:
            if "bus" in rc:
                ref = rc["ref"].split("::")[-1]
                cands = self.consts.get(ref)
                uniq = {v for _, v in cands} if cands else set()
                if len(uniq) == 1:
                    ex.edges.append(
                        Edge(
                            rc["bus"],
                            rc["src"],
                            next(iter(uniq)),
                            rc["line"],
                            "lexical",
                            f"{rc['ref']} = {next(iter(uniq))!r}",
                        )
                    )
                else:
                    ex.edges.append(
                        Edge(
                            rc["bus"],
                            rc["src"],
                            f"<{rc['ref']}>",
                            rc["line"],
                            "unresolved",
                            f"subject expression {rc['ref']}",
                        )
                    )
                continue
            name = rc["name"]
            if rc["local"]:
                tgt = next(
                    (
                        s.qualname
                        for s in ex.symbols
                        if s.name == name and s.kind in ("fn", "function", "method")
                    ),
                    name,
                )
                ex.edges.append(
                    Edge("CALLS", rc["src"], tgt, rc["line"], "lexical", "", path)
                )

    # markdown: links and path mentions
    def resolve_doc(self, ex: Extract) -> None:
        here = ex.path.rsplit("/", 1)[0] if "/" in ex.path else ""
        all_paths = self.all_paths
        for imp in ex.raw_imports:
            if "doc_link" in imp:
                tgt = _norm(f"{here}/{imp['doc_link']}")
                ok = tgt in all_paths or any(p.startswith(tgt + "/") for p in all_paths)
                ex.edges.append(
                    Edge(
                        "DOC_LINKS",
                        "<doc>",
                        tgt,
                        imp["line"],
                        "proven" if ok else "unresolved",
                        imp["doc_link"],
                        tgt if tgt in all_paths else None,
                    )
                )
            else:
                p = imp["doc_path"]
                cands = [p] + [q for q in (f"backend/{p}",) if q in all_paths]
                tgt = next((c for c in cands if c in all_paths), None)
                if tgt is None and "*" not in p:
                    tail = [q for q in all_paths if q.endswith("/" + p)]
                    tgt = tail[0] if len(tail) == 1 else None
                ex.edges.append(
                    Edge(
                        "DOC_MENTIONS",
                        "<doc>",
                        tgt or p,
                        imp["line"],
                        "inferred" if tgt else "unresolved",
                        p,
                        tgt if tgt in all_paths else None,
                    )
                )

    # js/ts
    def resolve_js(self, ex: Extract) -> None:
        path = ex.path
        here = path.rsplit("/", 1)[0]
        root = path.split("/")[0]
        for imp in ex.raw_imports:
            spec = imp["spec"]
            tgt = None
            if spec.startswith("."):
                base = _norm(f"{here}/{spec}")
            elif spec.startswith("@/"):
                base = f"{root}/{spec[2:]}"
            else:
                base = None
            if base:
                for cand in (
                    base,
                    f"{base}.ts",
                    f"{base}.tsx",
                    f"{base}.js",
                    f"{base}.jsx",
                    f"{base}.mjs",
                    f"{base}/index.ts",
                    f"{base}/index.tsx",
                    f"{base}/index.js",
                ):
                    if cand in self.files:
                        tgt = cand
                        break
                conf = "lexical" if tgt else "unresolved"
            else:
                conf = "external"
            ex.edges.append(
                Edge("IMPORTS", "<module>", tgt or spec, imp["line"], conf, spec, tgt)
            )
        if _is_test(path):
            for e in list(ex.edges):
                if e.kind == "IMPORTS" and e.dst_path and not _is_test(e.dst_path):
                    ex.edges.append(
                        Edge(
                            "TEST_VALIDATES",
                            path,
                            e.dst_path,
                            e.line,
                            "lexical",
                            e.evidence,
                            e.dst_path,
                        )
                    )


def _norm(p: str) -> str:
    out: list[str] = []
    for part in p.split("/"):
        if part == "..":
            if out:
                out.pop()
        elif part not in (".", ""):
            out.append(part)
    return "/".join(out)


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        "/tests/" in path
        or "/test/" in path
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".test.js"))
    )


# ---------------------------------------------------------------- build


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)


@dataclass
class IndexResult:
    parsed: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped_stale: list[str] = field(default_factory=list)
    symbols: int = 0
    edges: int = 0


def build_index(conn: sqlite3.Connection, root: Path) -> IndexResult:
    """Parse every in-scope parseable file whose bytes still match the inventory hash."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, path, sha256, language FROM files WHERE in_scope=1 AND present=1 AND language IN ({})".format(
            ",".join("?" * len(PARSED_LANGS))
        ),
        sorted(PARSED_LANGS),
    ).fetchall()
    res = IndexResult()
    extracts: dict[str, Extract] = {}
    ids: dict[str, int] = {}
    hashes: dict[str, str] = {}
    import hashlib

    for r in rows:
        p = root / r["path"]
        try:
            data = p.read_bytes()
        except OSError as exc:
            res.failed.append((r["path"], f"unreadable: {exc}"))
            continue
        if hashlib.sha256(data).hexdigest() != r["sha256"]:
            res.skipped_stale.append(r["path"])
            continue
        text = data.decode("utf-8", errors="replace")
        lang = r["language"]
        try:
            if lang == "python":
                ex = extract_python(r["path"], text)
            elif lang == "rust":
                ex = extract_rust(r["path"], text)
            elif lang in ("javascript", "typescript"):
                ex = extract_js(r["path"], text, lang)
            elif lang == "sql":
                ex = extract_sql(r["path"], text)
            else:
                ex = extract_other(r["path"], text, lang)
        except Exception as exc:  # a parser bug must be recorded, not hidden
            res.failed.append((r["path"], f"{type(exc).__name__}: {exc}"))
            continue
        if ex.error:
            res.failed.append((r["path"], ex.error))
            continue
        extracts[r["path"]] = ex
        ids[r["path"]] = r["id"]
        hashes[r["path"]] = r["sha256"]
    present = {r[0] for r in conn.execute("SELECT path FROM files WHERE present=1")}
    rs = Resolver(extracts, ids, present)
    for p, ex in extracts.items():
        if ex.language == "python":
            rs.resolve_python(ex)
        elif ex.language == "rust":
            rs.resolve_rust(ex)
        elif ex.language in ("javascript", "typescript"):
            rs.resolve_js(ex)
        elif ex.language == "markdown":
            rs.resolve_doc(ex)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM symbols")
        for p, ex in extracts.items():
            fid, sha = ids[p], hashes[p]
            conn.executemany(
                "INSERT INTO symbols (file_id, sha256, kind, name, qualname, line, end_line, decorators) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        fid,
                        sha,
                        s.kind,
                        s.name,
                        s.qualname,
                        s.line,
                        s.end_line,
                        s.decorators,
                    )
                    for s in ex.symbols
                ],
            )
            conn.executemany(
                "INSERT INTO edges (file_id, sha256, kind, src, dst, dst_file_id, line, confidence, evidence) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        fid,
                        sha,
                        e.kind,
                        e.src,
                        e.dst,
                        ids.get(e.dst_path) if e.dst_path else None,
                        e.line,
                        e.confidence,
                        e.evidence,
                    )
                    for e in ex.edges
                ],
            )
            conn.execute(
                "UPDATE files SET parser=?, parsed_hash=?, indexed_hash=?, failure_stage=NULL, failure_hash=NULL, failure_msg=NULL WHERE id=?",
                (PARSER_USED.get(ex.language, "line-count"), sha, sha, fid),
            )
            res.parsed += 1
            res.symbols += len(ex.symbols)
            res.edges += len(ex.edges)
        for p, msg in res.failed:
            row = conn.execute(
                "SELECT id, sha256 FROM files WHERE path=?", (p,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE files SET failure_stage='parse', failure_hash=?, failure_msg=? WHERE id=?",
                    (row["sha256"], msg[:300], row["id"]),
                )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return res


# ---------------------------------------------------------------- queries


def find_symbols(conn, name: str, limit: int = 40):
    return conn.execute(
        "SELECT f.path, s.kind, s.qualname, s.line, s.end_line, s.decorators FROM symbols s JOIN files f ON f.id=s.file_id "
        "WHERE s.name=? OR s.qualname=? OR s.qualname LIKE ? ORDER BY f.path, s.line LIMIT ?",
        (name, name, f"%.{name}", limit),
    ).fetchall()


def refs_to(conn, name: str, limit: int = 60):
    """Incoming edges to a symbol, grouped by confidence."""
    last = name.rsplit(".", 1)[-1]
    return conn.execute(
        "SELECT f.path AS src_path, e.kind, e.src, e.dst, e.line, e.confidence, t.path AS dst_path "
        "FROM edges e JOIN files f ON f.id=e.file_id LEFT JOIN files t ON t.id=e.dst_file_id "
        "WHERE e.kind IN ('CALLS','STARTS_TASK','IMPORTS','INHERITS','DECORATED_BY','TEST_VALIDATES') "
        "AND (e.dst=? OR e.dst=? OR e.dst LIKE ? OR e.evidence LIKE ?) ORDER BY e.confidence, f.path, e.line LIMIT ?",
        (name, last, f"%.{last}", f"% import {last}%", limit),
    ).fetchall()


def file_deps(conn, path: str, reverse: bool = False):
    if reverse:
        return conn.execute(
            "SELECT f.path AS other, e.kind, e.line, e.confidence FROM edges e JOIN files f ON f.id=e.file_id "
            "JOIN files t ON t.id=e.dst_file_id WHERE t.path=? AND e.kind IN ('IMPORTS','TEST_VALIDATES') ORDER BY f.path",
            (path,),
        ).fetchall()
    return conn.execute(
        "SELECT t.path AS other, e.kind, e.line, e.confidence FROM edges e JOIN files f ON f.id=e.file_id "
        "JOIN files t ON t.id=e.dst_file_id WHERE f.path=? AND e.kind IN ('IMPORTS','TEST_VALIDATES') ORDER BY t.path",
        (path,),
    ).fetchall()


def subject_matrix(conn):
    """subject -> publishers and subscribers with confidence."""
    rows = conn.execute(
        "SELECT e.dst AS subject, e.kind, f.path, e.src, e.line, e.confidence FROM edges e JOIN files f ON f.id=e.file_id "
        "WHERE e.kind IN ('PUBLISHES','SUBSCRIBES') ORDER BY e.dst, e.kind, f.path"
    ).fetchall()
    out: dict[str, dict[str, list]] = {}
    for r in rows:
        d = out.setdefault(r["subject"], {"PUBLISHES": [], "SUBSCRIBES": []})
        d[r["kind"]].append((r["path"], r["src"], r["line"], r["confidence"]))
    return out


def unreferenced(conn, kind: str = "function"):
    """Symbols with no incoming static edge. NOT a list of dead code: see DYNAMIC counts."""
    dyn = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='DYNAMIC'").fetchone()[0]
    rows = conn.execute(
        "SELECT f.path, s.qualname, s.line FROM symbols s JOIN files f ON f.id=s.file_id "
        "WHERE s.kind=? AND f.path NOT LIKE '%/tests/%' AND f.path NOT LIKE 'tests/%' AND s.name NOT LIKE '\\_%' ESCAPE '\\' AND s.decorators='' "
        "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.kind IN ('CALLS','STARTS_TASK') AND e.dst IN (s.name, s.qualname) AND e.dst_file_id=s.file_id) "
        "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.kind='IMPORTS' AND e.evidence LIKE '% import ' || s.name || '%') "
        "ORDER BY f.path, s.line",
        (kind,),
    ).fetchall()
    return rows, dyn


def stats(conn) -> dict:
    d = {}
    d["symbols_by_kind"] = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT kind, COUNT(*) FROM symbols GROUP BY kind ORDER BY 2 DESC"
        )
    }
    d["edges_by_kind_confidence"] = {
        f"{r[0]}/{r[1]}": r[2]
        for r in conn.execute(
            "SELECT kind, confidence, COUNT(*) FROM edges GROUP BY kind, confidence ORDER BY 1, 2"
        )
    }
    d["parsed"] = conn.execute(
        "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1 AND parsed_hash=sha256"
    ).fetchone()[0]
    d["parseable"] = conn.execute(
        "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1 AND language IN ({})".format(
            ",".join("?" * len(PARSED_LANGS))
        ),
        sorted(PARSED_LANGS),
    ).fetchone()[0]
    d["in_scope"] = conn.execute(
        "SELECT COUNT(*) FROM files WHERE in_scope=1 AND present=1"
    ).fetchone()[0]
    return d
