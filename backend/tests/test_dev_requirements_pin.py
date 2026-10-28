"""Guard the pytest cap in requirements-dev.txt.

pytest 9.2.0 raises ``AttributeError: 'Function' object has no attribute
'_nodeid'`` inside pytest-benchmark 5.3.0, so every ``benchmark`` fixture test
errors at setup and CI goes red while the other tests pass. The cap keeps CI
installs on a working pair. Remove this test together with the cap once
pytest-benchmark supports pytest 9.2.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements-dev.txt"


def _pytest_spec() -> str:
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if re.match(r"^pytest\s*($|[<>=!~])", line.strip()):
            return line.strip()
    raise AssertionError("requirements-dev.txt has no bare `pytest` requirement line")


def test_requirements_dev_caps_pytest_below_9_2() -> None:
    assert _pytest_spec().replace(" ", "") == "pytest<9.2"


def test_installed_pytest_pairs_with_pytest_benchmark(benchmark) -> None:
    # Fails at fixture setup (AttributeError '_nodeid') on the broken pair.
    assert benchmark(lambda: sum(range(10))) == 45
    assert int(pytest.__version__.split(".")[0]) >= 8
