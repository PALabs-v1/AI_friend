"""Keeps `scripts/barge_in_mutations.py` in step with the code it mutates.

The mutation check itself takes minutes and runs on demand; this gate only
asserts that every mutation's pattern still occurs exactly once, so a
refactor cannot silently turn a mutation into a no-op that "survives"."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "barge_in_mutations.py"


def _load():
    spec = importlib.util.spec_from_file_location("barge_in_mutations", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def test_every_mutation_pattern_matches_the_source_exactly_once():
    assert _load().check_patterns() == []


def test_mutations_are_uniquely_named_and_equivalents_say_why():
    mutations = _load().MUTATIONS
    assert len({m.name for m in mutations}) == len(mutations)
    assert all(m.equivalent is None or len(m.equivalent) > 20 for m in mutations)


def test_only_failing_tests_count_as_a_kill():
    """R8: with `-x`, a collection error came back as exit 1 and was counted
    as a kill; a hung run gave no verdict at all."""
    classify = _load().classify
    assert classify(1, "1 failed, 27 passed in 6.1s") == "killed"
    assert classify(0, "28 passed in 6.1s") == "passed"
    assert classify(1, "1 error in 0.21s") == "error"  # collection error under -x
    assert classify(2, "Interrupted: 2 errors during collection") == "error"
    assert classify(1, "1 failed, 26 passed, 1 error in 6.0s") == "error"
    assert classify(5, "no tests ran in 0.01s") == "error"
    assert classify(None, "") == "error"  # timed out


def test_the_runner_actually_used_has_no_x_and_a_timeout(monkeypatch):
    """R9: a stale second `_run_tests` (with `-x`, no timeout) shadowed the
    fixed one, so the documented behaviour never ran. Pin the function the
    module really binds, through the call it makes."""
    import subprocess

    tool = _load()
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    assert tool._run_tests(Path(".")) is None  # a hang is reported, not waited out
    args, kwargs = calls[0]
    assert "-x" not in args
    assert kwargs["timeout"] == tool.RUN_TIMEOUT_S
    assert tool.classify(None, "") == "error"
    names = [n for n in vars(tool) if not n.startswith("__")]
    assert len(names) == len(set(names))
    source = SCRIPT.read_text()
    for fn in ("_run_tests", "_copy_backend", "classify", "main", "check_patterns"):
        assert source.count(f"\ndef {fn}(") == 1, fn  # no shadowed duplicate
