"""Slice 2a gate tests: the bare arm bypasses the pipeline; the no-LLM
baselines are deterministic, scoped honestly, and still show the defects they
exist to record."""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.llm.model_roles import ModelRole
from evals.brainbench import bare_model
from evals.brainbench.baseline import defects

BACKEND = Path(__file__).resolve().parents[1]
COMMITTED = BACKEND.parent / "docs/brain-v4/results/slice-02a/defect_baselines.json"

# The complete set of places that write the baseline PAD today. A new writer,
# anywhere in backend/app, must show up here on purpose.
EXPECTED_PAD_WRITERS = {
    "assignment_sites": [
        "app/state/agent_state.py::_apply_redis_state",
        "app/state/agent_state.py::_apply_sqlite_state",
        "app/state/agent_state.py::_hydrate_locked",
        "app/state/agent_state.py::apply_external_state",
        "app/state/agent_state.py::personality_baseline_affect",
    ],
    "constructor_seed_sites": ["app/state/agent_state.py::__init__"],
    "store_write_string_sites": [
        "app/agents/subconscious_agent.py::_on_state_broadcast",
        "app/state/agent_state.py::_write_state_row",
    ],
}


class RecordingLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate(
        self, prompt, system=None, model=None, options_override=None, role=None
    ):
        self.calls.append(
            {"prompt": prompt, "system": system, "model": model, "role": role}
        )
        return "ok"


# --- the bare-model arm ------------------------------------------------------


def test_bare_arm_source_has_no_pipeline_imports_or_dynamic_imports():
    tree = ast.parse(Path(bare_model.__file__).read_text("utf-8"))
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            names = [f"{mod}.{a.name}" for a in n.names]
            if mod.startswith(("app.cognitive", "app.state", "app.agents")) or any(
                x.startswith(("app.cognitive", "app.state", "app.agents"))
                for x in names
            ):
                bad.append(ast.unparse(n))
        elif isinstance(n, ast.Import):
            bad += [
                a.name
                for a in n.names
                if a.name.startswith(("app.cognitive", "app.state", "app.agents"))
            ]
        elif isinstance(n, ast.Name) and n.id in {"importlib", "__import__"}:
            bad.append(n.id)
        elif isinstance(n, ast.Attribute) and n.attr == "import_module":
            bad.append("import_module")
    assert not bad, f"bare arm must not reach the pipeline: {bad}"


def test_importing_the_bare_arm_loads_no_pipeline_module():
    """Catches transitive imports the source scan cannot see. The file is loaded
    by path: `import evals` itself pulls in `app.cognitive` through the package
    init, which says nothing about the arm."""
    code = (
        "import importlib.util, sys;"
        f"spec=importlib.util.spec_from_file_location('bare', {str(Path(bare_model.__file__))!r});"
        "m=importlib.util.module_from_spec(spec); sys.modules['bare']=m; spec.loader.exec_module(m);"
        "bad=sorted(x for x in sys.modules if x.startswith("
        "('app.cognitive','app.state','app.agents')));"
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env={"PYTHONPATH": str(BACKEND), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"pipeline modules loaded: {proc.stdout.strip()}{proc.stderr[-300:]}"
    )


def test_bare_arm_sends_persona_and_the_whole_history_in_order():
    llm = RecordingLLM()
    arm = bare_model.BareModelArm(llm=llm, persona_text="You are Mira.", model="m")
    history = []
    for i in range(40):
        history += [("user", f"u{i}"), ("assistant", f"a{i}")]
    assert asyncio.run(arm.reply(history, "latest")) == "ok"
    (call,) = llm.calls
    assert call["system"] == "You are Mira."
    assert call["role"] is ModelRole.REALIZATION
    lines = call["prompt"].splitlines()
    assert lines[:-2] == [f"{s}: {t}" for s, t in history]
    assert lines[-2:] == ["user: latest", "assistant:"]


def test_bare_arm_makes_exactly_one_model_call_per_turn():
    llm = RecordingLLM()
    arm = bare_model.BareModelArm(llm=llm, persona_text="p")
    asyncio.run(arm.reply([], "hi"))
    asyncio.run(arm.reply([("user", "hi"), ("assistant", "ok")], "again"))
    assert len(llm.calls) == 2


def test_switchboard_names_the_bare_arm_instead_of_calling_it_unknown():
    from evals.brainbench.switchboard import ArmUnavailable, resolve_arm

    with pytest.raises(ArmUnavailable, match="BareModelArm"):
        resolve_arm("bare-model")


# --- determinism ------------------------------------------------------------


def _run_collect() -> dict:
    return asyncio.run(defects.collect())


@pytest.fixture(scope="module")
def baseline():
    return _run_collect()


def test_baselines_are_deterministic_in_process(baseline):
    assert defects.digest(baseline) == defects.digest(_run_collect())


def test_baselines_are_deterministic_across_processes_and_working_directories(
    tmp_path,
):
    outputs = []
    for i, cwd in enumerate((BACKEND, tmp_path)):
        out = tmp_path / f"run{i}"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "evals.brainbench.baseline.defects",
                "--out",
                str(out),
            ],
            cwd=cwd,
            env={"PYTHONPATH": str(BACKEND), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        outputs.append((out / "defect_baselines.json").read_bytes())
    assert outputs[0] == outputs[1]


def test_output_holds_no_absolute_path_or_checkout_location(baseline):
    text = defects.canonical(baseline)
    assert str(BACKEND) not in text and str(BACKEND.parent) not in text
    assert "/Users/" not in text and "/tmp" not in text


def test_the_shuffle_seeds_are_part_of_the_digest(baseline):
    orders = baseline["mood_self_feed"]["runs"]["start_mood_0.5"]["orders"]
    sequences = {name: tuple(run["text_order"]) for name, run in orders.items()}
    assert len(set(sequences.values())) == len(sequences), "orders must differ"
    assert sequences["real"] == tuple(range(len(defects.MOOD_TEXTS)))


def test_frozen_scenario_matches_the_committed_baseline(baseline):
    committed = json.loads(COMMITTED.read_text("utf-8"))
    assert (
        committed["envelope"]["scenario_sha256"]
        == defects.envelope(baseline)["scenario_sha256"]
    ), "frozen inputs were edited after the baseline run; new preregistration needed"


def test_scenario_digest_changes_when_a_text_changes(baseline):
    changed = dict(baseline["scenario"])
    changed["hostile"] = changed["hostile"] + "!"
    assert defects.digest(changed) != defects.digest(baseline["scenario"])


# --- what the baselines show (and no more than they show) ---------------------


def test_scope_statement_is_recorded_with_the_mood_baseline(baseline):
    scope = baseline["mood_self_feed"]["scope"]
    assert "System 2" in scope and "sensory" in scope and "voice" in scope


def test_on_the_heuristic_text_only_path_mood_depends_only_on_prior_mood(baseline):
    runs = baseline["mood_self_feed"]["runs"]
    for run in runs.values():
        assert all(g == 0.0 for g in run["max_abs_gap_real_vs_each_shuffled"].values())
    zero = runs["start_mood_0.0"]["orders"]["real"]["mood_trajectory"]
    assert set(zero) == {0.0}
    ratios = runs["start_mood_0.5"]["orders"]["real"]["mood_ratio_per_turn"]
    assert len({round(r, 9) for r in ratios}) == 1
    distinct = baseline["mood_self_feed"]["distinct_goal_congruence_values_per_bias"]
    assert set(distinct.values()) == {1}


def test_hostile_text_raises_integrity_exactly_like_support(baseline):
    runs = baseline["integrity_on_hostile_text"]
    assert runs["hostile"] == runs["supportive"] == runs["neutral"]
    assert all(t["norm_alignment"] == 1.0 for t in runs["hostile"][1:])
    assert (
        runs["hostile"][-1]["trust_integrity"] > runs["hostile"][0]["trust_integrity"]
    )


def test_the_behavioral_term_argument_is_a_constant(baseline):
    b = baseline["behavioral_signal"]
    assert b["values_passed_at_call_sites"] == [0.5]
    assert b["weights"]["behavioral"] == 0.2


def test_baseline_pad_writers_are_exactly_the_known_set(baseline):
    assert baseline["baseline_pad_writers"] == EXPECTED_PAD_WRITERS


@pytest.mark.parametrize(
    "source",
    [
        "obj.baseline_valence = 0.3",
        "a, obj.baseline_arousal = 1, 2",
        "obj.baseline_dominance += 0.1",
        "setattr(obj, 'baseline_valence', 0.3)",
        "obj.personality_baseline_affect = {}",
    ],
)
def test_pad_scan_catches_attribute_tuple_augmented_and_setattr_writes(source):
    found = defects.pad_write_sites(ast.parse(f"def f(obj):\n    {source}"), "x.py")
    assert found["assignment"] == {"x.py::f"}


def test_pad_scan_catches_replace_seeds_and_cypher_writes():
    tree = ast.parse(
        "def f(s):\n"
        "    dataclasses.replace(s, baseline_valence=0.2)\n"
        "    q = 'MATCH (a) SET a.baseline_valence = $v'\n"
    )
    found = defects.pad_write_sites(tree, "x.py")
    assert found["seed"] == {"x.py::f"}
    assert found["store_string"] == {"x.py::f"}


def test_pad_scan_ignores_prose_that_mentions_a_pad_name():
    tree = ast.parse(
        "def f():\n    return 'we set baseline_valence from the persona'\n"
    )
    assert defects.pad_write_sites(tree, "x.py")["store_string"] == set()


@pytest.mark.parametrize(
    "source",
    [
        "bus.subscribe(Topics.STATE_SUBCONSCIOUS, cb)",
        "bus.subscribe('state.subconscious', cb)",
        "subj = Topics.STATE_SUBCONSCIOUS\nbus.subscribe(subj, cb)",
        "bus.subscribe('state.>', cb)",
        "bus.subscribe('*.subconscious', cb)",
        "bus.subscribe('>', cb)",
    ],
)
def test_subject_scan_finds_direct_aliased_and_wildcard_subscriptions(source):
    roles = defects.subject_roles(
        ast.parse(source), "state.subconscious", "STATE_SUBCONSCIOUS"
    )
    assert roles["subscribes"] is True


def test_subject_scan_does_not_match_an_unrelated_subject():
    roles = defects.subject_roles(
        ast.parse("bus.subscribe('audio.>', cb)\nbus.publish('chat.output', x)"),
        "state.subconscious",
        "STATE_SUBCONSCIOUS",
    )
    assert roles == {"publishes": False, "subscribes": False}


def test_unread_subjects_have_publishers_and_no_subscribers(baseline):
    for subject, row in baseline["subscriber_counts"].items():
        assert row["python_publishers"], subject
        assert row["python_subscribers"] == [], subject
        assert "not frontend or scripts" in row["scan_scope"]
