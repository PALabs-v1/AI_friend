from __future__ import annotations

import pytest

from tools.repo_intel.scope import classify, expand_braces, glob_to_regex, load_rules


def test_brace_expansion():
    assert sorted(expand_braces("*.{py,rs}")) == ["*.py", "*.rs"]
    assert sorted(expand_braces("{a,b}/x.{c,d}")) == [
        "a/x.c",
        "a/x.d",
        "b/x.c",
        "b/x.d",
    ]
    assert expand_braces("plain") == ["plain"]


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("*.py", "a/b/c.py", True),  # no slash: matches the name at any depth
        ("*.py", "c.pyc", False),
        ("**/tests/**", "backend/tests/test_x.py", True),
        ("**/tests/**", "tests/x.py", True),
        ("**/tests/**", "backend/attests/x.py", False),
        ("docs/*.md", "docs/a.md", True),
        ("docs/*.md", "docs/sub/a.md", False),
        ("docs/**", "docs/sub/a.md", True),
        (".*", ".gitignore", True),
        (".*", "dir/.hidden", True),
        (".*", "visible", False),
    ],
)
def test_glob_semantics(pattern, path, expected):
    assert any(glob_to_regex(p).match(path) for p in expand_braces(pattern)) is expected


def verdict(path, **kw):
    rules, _ = load_rules()
    base = {"kind": "file", "is_binary": False, "present": True, "rules": rules}
    return classify(path, **{**base, **kw})


def test_kind_rules_run_before_path_rules():
    assert verdict("src/x.py", is_binary=True).category == "BINARY_ASSET"
    assert verdict("src/x.py", kind="symlink").category == "SYMLINK"
    assert verdict("src/x.py", kind="submodule").category == "SUBMODULE"
    assert verdict("src/x.py", present=False).category == "MISSING"


def test_skill_entry_is_read_but_payload_is_not():
    assert verdict(".claude/skills/pdf/SKILL.md").in_scope
    payload = verdict(".claude/skills/pdf/scripts/extract.py")
    assert not payload.in_scope and payload.category == "VENDORED_SKILL_PAYLOAD"
    assert payload.reason, "every exclusion must say why"


def test_every_exclusion_has_a_reason_and_nothing_is_dropped_silently():
    rules, _ = load_rules()
    for r in rules:
        if not r.in_scope:
            assert r.reason.strip(), f"rule {r.name} excludes files without a reason"
    assert verdict(
        "some/unknown.weirdext"
    ).in_scope  # fallback keeps unknown files in scope


def test_roles():
    assert verdict("backend/tests/test_x.py").category == "TEST"
    assert verdict("backend/app/x.py").category == "SOURCE"
    assert verdict("backend/scripts/x.py").category == "SCRIPT"
    assert verdict("backend/db/schema.sql").category == "SCHEMA"
    assert verdict(".github/workflows/ci.yml").category == "CI_BUILD"
    assert verdict("docs/a.md").category == "DOC"
    assert verdict("frontend/package-lock.json").category == "LOCKFILE"
    assert (
        verdict("docs/brain-research-v3/results/x/report.json").category
        == "GENERATED_RESULT"
    )
    assert verdict("docs/brain-research-v3/results/x/report.md").category == "DOC"
