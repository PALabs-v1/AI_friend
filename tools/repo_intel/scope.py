"""Scope classification: which files must be read, and why the others need not be.

Rules live in scope.toml (ordered, first match wins). Kind-based rules run
first because they depend on what the file is, not where it sits.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

SCOPE_FILE = Path(__file__).with_name("scope.toml")

LANGUAGES = {
    ".py": "python",
    ".rs": "rust",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".swift": "swift",
    ".sql": "sql",
    ".sh": "shell",
    ".ps1": "powershell",
    ".bat": "batch",
    ".md": "markdown",
    ".json": "json",
    ".jsonl": "jsonl",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".ipynb": "jupyter",
}

# The parser planned for a language in the structural phase. Recording it here
# does not mean the file was parsed; the PARSED state is set by the indexer.
PARSERS = {
    "python": "python-ast",
    "rust": "rust-analyzer",
    "javascript": "js-lexical",
    "typescript": "js-lexical",
    "sql": "sql-lexical",
    "json": "json",
    "jsonl": "jsonl",
    "yaml": "yaml-lexical",
    "toml": "toml",
    "markdown": "markdown-headings",
}


def expand_braces(pattern: str) -> list[str]:
    """Expand one level of {a,b} groups, recursively. No nesting inside a group."""
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out.extend(expand_braces(pattern[: m.start()] + alt + pattern[m.end() :]))
    return out


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a scope glob to an anchored regex over a POSIX relative path."""
    anywhere = "/" not in pattern
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    body = "".join(out)
    if anywhere:
        body = "(?:.*/)?" + body
    return re.compile(rf"\A{body}\Z", re.DOTALL)


@dataclass(frozen=True)
class Rule:
    name: str
    patterns: tuple[re.Pattern[str], ...]
    category: str
    in_scope: bool
    reason: str


@dataclass(frozen=True)
class Verdict:
    category: str
    language: str
    parser: str
    in_scope: bool
    reason: str
    rule: str


def load_rules(path: Path = SCOPE_FILE) -> tuple[list[Rule], str]:
    raw = path.read_bytes()
    doc = tomllib.loads(raw.decode("utf-8"))
    rules = []
    for r in doc["rule"]:
        pats = tuple(glob_to_regex(p) for p in expand_braces(r["glob"]))
        rules.append(
            Rule(
                r["name"], pats, r["category"], bool(r["in_scope"]), r.get("reason", "")
            )
        )
    return rules, hashlib.sha256(raw).hexdigest()


def language_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    return LANGUAGES.get(suffix, "text")


def classify(
    path: str,
    *,
    kind: str,
    is_binary: bool,
    present: bool,
    rules: list[Rule],
    symlink_target_in_repo: bool = True,
) -> Verdict:
    language = language_of(path)
    parser = PARSERS.get(language, "text")
    if not present:
        return Verdict(
            "MISSING",
            language,
            "none",
            False,
            "Listed by git but absent from the working tree.",
            "kind:missing",
        )
    if kind == "submodule":
        return Verdict(
            "SUBMODULE",
            "none",
            "none",
            False,
            "Git submodule; its content belongs to another repository.",
            "kind:submodule",
        )
    if kind == "symlink":
        why = (
            "Symbolic link; its target is inventoried as its own file."
            if symlink_target_in_repo
            else "Symbolic link to a target outside the repository; not followed."
        )
        return Verdict("SYMLINK", "none", "none", False, why, "kind:symlink")
    if is_binary:
        return Verdict(
            "BINARY_ASSET",
            "binary",
            "none",
            False,
            "Binary content (NUL byte in the first 8 KiB). Hashed, not read as text.",
            "kind:binary",
        )
    for rule in rules:
        if any(p.match(path) for p in rule.patterns):
            return Verdict(
                rule.category, language, parser, rule.in_scope, rule.reason, rule.name
            )
    raise AssertionError(f"scope.toml has no fallback rule matching {path!r}")
