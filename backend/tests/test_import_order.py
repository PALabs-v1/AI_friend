"""Every top-level package imports cleanly on its own, in a fresh interpreter.

Regression: W9 made app.state.agent_state import app.cognitive.goals, and
app/cognitive/__init__.py eagerly imported core, which imports app.state, so
`import app.state` before anything else raised a circular ImportError. The
suite only saw it when an integration test happened to import state first;
an agent or script with that import order would crash at startup.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module",
    [
        "app.state",
        "app.state.agent_state",
        "app.cognitive",
        "app.cognitive.goals",
        "app.cognitive.core",
        "app.agents.brain_agent",
        "app.agents.subconscious_agent",
    ],
)
def test_module_imports_first_in_a_fresh_interpreter(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_lazy_reexports_still_resolve():
    from app.cognitive import CognitiveService, ReflectionService
    from app.cognitive.core import CognitiveService as direct

    assert CognitiveService is direct
    assert ReflectionService.__name__ == "ReflectionService"
