"""`scripts/research/bb_remote.sh` in dry-run mode: no ssh, no network.

The live behaviour (exit-code propagation, killed-job detection, duplicate
refusal) was verified against home-gpu when the script was written; this
pins the parts that can be checked offline, above all the quoting of the job
script, which is where the first version broke.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "research" / "bb_remote.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "BB_DRY_RUN": "1", "GPU_HOST": "box"},
        check=False,
    )


def test_job_arguments_survive_quoting_exactly_once():
    args = ["python3", "-c", "print(1)", "arg with spaces", 'and $HOME "quotes"']
    result = _run("run", "job1", "--", *args)
    assert result.returncode == 0, result.stderr
    job_line = result.stdout.splitlines()[2]  # "script for job1:", "cd ...", job
    assert shlex.split(job_line) == args


def test_script_records_the_exit_code_and_runs_in_backend():
    out = _run("run", "job1", "--", "true").stdout
    assert "cd /data/aif-v3/backend" in out
    assert "echo $? > /data/aif-v3/run-logs/job1.log.exit" in out


def test_sync_keeps_run_outputs_out_of_the_clones_status():
    out = _run("sync", "brain-v3").stdout
    assert ".git/info/exclude" in out
    for entry in ("runs/", "run-logs/"):
        assert entry in out


def test_unsafe_run_names_are_refused():
    for name in ("../x", "a b", "x;rm", ""):
        assert _run("run", name, "--", "true").returncode == 2


def test_run_without_a_command_is_refused():
    assert _run("run", "job1", "--").returncode == 2


def test_sync_is_fast_forward_only():
    out = _run("sync", "brain-v3").stdout
    assert "merge -q --ff-only origin/brain-v3" in out


def test_run_starts_the_job_in_its_own_process_group():
    # stop kills the group; without setsid the pool workers outlive a stop.
    out = _run("run", "job1", "--", "true").stdout
    assert "setsid nohup bash /data/aif-v3/run-logs/job1.sh" in out


def test_stop_signals_the_group_and_the_whole_tree_then_records_an_exit():
    out = _run("stop", "job1").stdout
    assert "kill -TERM -- -$p" in out
    assert "pgrep -d , -P" in out  # tree collected before any signal
    assert out.index("pgrep") < out.index("kill -TERM")
    assert "kill -KILL $tree" in out
    assert "echo 143 > /data/aif-v3/run-logs/job1.log.exit" in out


def test_stop_refuses_unsafe_names():
    assert _run("stop", "x;rm").returncode == 2


def test_usage_on_unknown_command():
    result = _run("bogus")
    assert result.returncode == 2
    assert "sync [branch]" in result.stdout
