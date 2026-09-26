"""
P1-6: a Postgres connection failure at bootstrap used to log at WARNING and
silently fall back to SQLite, losing pgvector with no other signal. Q-M2-2
answered SQLite as emergency-only, not a supported runtime mode, so entering
it unnoticed is the actual defect -- M2-P3's synchronous SQLite I/O then
blocks the whole mesh's event loop in a mode nobody knows it entered.
"""

import json
import logging

import pytest

from app import config as config_module
from app.runtime_bootstrap import (
    _clear_sqlite_fallback,
    _enter_sqlite_fallback,
    _model_exists,
    _normalized_required_models,
)


@pytest.fixture(autouse=True)
def _reset_environment(monkeypatch):
    """Every test starts from a known ENVIRONMENT/ALLOW_SQLITE_FALLBACK
    baseline regardless of what an earlier test (or the real .env) set."""
    monkeypatch.setattr(config_module.config_instance, "ENVIRONMENT", "development")
    monkeypatch.setattr(config_module.config_instance, "ALLOW_SQLITE_FALLBACK", False)


def test_fallback_logs_at_error_not_warning(monkeypatch, caplog):
    """The original bug was specifically that this was invisible at default
    log levels - WARNING is easy to filter out; ERROR is not."""
    with caplog.at_level(logging.ERROR, logger="runtime_bootstrap"):
        _enter_sqlite_fallback(reason="PostgreSQL connection failed: timeout")

    assert any(
        record.levelno == logging.ERROR
        and "PostgreSQL connection failed" in record.message
        for record in caplog.records
    )


def test_fallback_fails_closed_in_production_by_default(monkeypatch):
    """Q-M2-2: SQLite is emergency-only. A production deployment silently
    losing pgvector is worse than refusing to start."""
    monkeypatch.setattr(config_module.config_instance, "ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="Refusing to silently downgrade"):
        _enter_sqlite_fallback(reason="PostgreSQL connection failed: timeout")


def test_fallback_allowed_in_production_with_explicit_override(monkeypatch):
    """ALLOW_SQLITE_FALLBACK is the documented escape hatch for a deliberate
    degraded production deployment - it must not fail closed."""
    monkeypatch.setattr(config_module.config_instance, "ENVIRONMENT", "production")
    monkeypatch.setattr(config_module.config_instance, "ALLOW_SQLITE_FALLBACK", True)

    _enter_sqlite_fallback(reason="PostgreSQL connection failed: timeout")


def test_fallback_does_not_fail_closed_outside_production(monkeypatch):
    monkeypatch.setattr(config_module.config_instance, "ENVIRONMENT", "development")

    _enter_sqlite_fallback(reason="PostgreSQL connection failed: timeout")


def test_fallback_writes_health_sentinel(tmp_path, monkeypatch):
    """A different process (main.py's /health) reads this file, so its
    shape - a JSON object with `reason` - is a real contract, not just a
    debugging aid."""
    sentinel = tmp_path / "sqlite_fallback_active"
    monkeypatch.setattr(
        config_module.config_instance, "SQLITE_FALLBACK_HEALTH_FILE", str(sentinel)
    )

    _enter_sqlite_fallback(reason="PostgreSQL connection failed: timeout")

    assert sentinel.exists()
    payload = json.loads(sentinel.read_text())
    assert payload["reason"] == "PostgreSQL connection failed: timeout"
    assert "timestamp" in payload


def test_recovery_clears_the_health_sentinel(tmp_path, monkeypatch):
    """The signal has to turn off again. Without this, one transient
    Postgres outage leaves /health reporting `degraded: true` for the
    lifetime of the host, long after Postgres came back - and a warning
    that never clears is one operators stop reading, which is the same
    silence this item set out to fix."""
    sentinel = tmp_path / "sqlite_fallback_active"
    monkeypatch.setattr(
        config_module.config_instance, "SQLITE_FALLBACK_HEALTH_FILE", str(sentinel)
    )

    _enter_sqlite_fallback(reason="PostgreSQL connection failed: timeout")
    assert sentinel.exists()

    _clear_sqlite_fallback()

    assert not sentinel.exists()


def test_clearing_an_absent_sentinel_is_not_an_error(tmp_path, monkeypatch):
    """The healthy path runs this on every boot, and on almost all of them
    there is no sentinel to remove. It must not raise into bootstrap."""
    monkeypatch.setattr(
        config_module.config_instance,
        "SQLITE_FALLBACK_HEALTH_FILE",
        str(tmp_path / "never_written"),
    )

    _clear_sqlite_fallback()


def test_normalized_required_models_dedupes_preserving_first_occurrence():
    """OLLAMA_REQUIRED_MODELS_STR is user-edited CSV in .env - a repeated
    entry must not turn into two pull requests for the same model."""
    result = _normalized_required_models(
        ["llama3.2:3b", "nomic-embed-text", "llama3.2:3b"]
    )
    assert result == ["llama3.2:3b", "nomic-embed-text"]


def test_normalized_required_models_strips_whitespace_and_drops_blanks():
    """A trailing comma in the .env value (`"a,b,"`) must not turn into a
    third, empty model name that then 404s against Ollama's pull API."""
    result = _normalized_required_models(["  llama3.2:3b  ", "", "   "])
    assert result == ["llama3.2:3b"]


def test_model_exists_exact_match():
    assert _model_exists("llama3.2:3b", ["llama3.2:3b", "nomic-embed-text"]) is True


def test_model_exists_returns_false_when_truly_absent():
    assert _model_exists("llama3.2:3b", ["nomic-embed-text"]) is False


def test_model_exists_treats_bare_name_as_already_satisfied_by_latest_tag():
    """Config lists a bare model name ("llama3.2"); Ollama's own /api/tags
    reports it tagged `:latest`. Without this the bootstrap would re-pull a
    model that is already present under its default tag on every boot."""
    assert _model_exists("llama3.2", ["llama3.2:latest"]) is True


def test_model_exists_treats_explicit_latest_as_satisfied_by_bare_name():
    """The inverse direction: config explicitly asks for `:latest`, Ollama
    reports the bare (untagged) name."""
    assert _model_exists("llama3.2:latest", ["llama3.2"]) is True


def test_model_exists_does_not_treat_different_tags_as_equivalent():
    """The `:latest` compatibility rule must not blur into "any tag counts"
    - a model pinned to a different tag is genuinely missing."""
    assert _model_exists("llama3.2:3b", ["llama3.2:7b"]) is False


# F-019: under default NATS auth the brain ran setup_streams with brain_agent
# credentials, which hold no $JS.API.STREAM.* rights, so every call waited out
# its timeout and bootstrap crash-looped. Only the provisioner provisions.


def _record_setup_streams(monkeypatch):
    import app.runtime_bootstrap as rb

    calls = []

    async def fake_setup_streams(url):
        calls.append(url)

    monkeypatch.setattr(rb, "setup_streams", fake_setup_streams)
    return rb, calls


@pytest.mark.parametrize("user", ["brain_agent", None])
def test_an_agent_identity_leaves_streams_to_the_provisioner(monkeypatch, user):
    import asyncio

    rb, calls = _record_setup_streams(monkeypatch)
    monkeypatch.delenv("NATS_PROVISIONER_USER", raising=False)
    if user is None:
        monkeypatch.delenv("NATS_USER", raising=False)
    else:
        monkeypatch.setenv("NATS_USER", user)
    asyncio.run(rb._ensure_nats_streams())
    assert calls == []


@pytest.mark.parametrize(
    ("user", "provisioner_env"),
    [("nats_provisioner", None), ("ops_admin", "ops_admin")],
)
def test_the_provisioner_identity_still_provisions(monkeypatch, user, provisioner_env):
    import asyncio

    rb, calls = _record_setup_streams(monkeypatch)
    monkeypatch.setenv("NATS_USER", user)
    if provisioner_env is None:
        monkeypatch.delenv("NATS_PROVISIONER_USER", raising=False)
    else:
        monkeypatch.setenv("NATS_PROVISIONER_USER", provisioner_env)
    asyncio.run(rb._ensure_nats_streams())
    assert len(calls) == 1


def test_every_stream_setup_invocation_runs_as_the_provisioner():
    """F-019, the deploy side: scripts/integration/deploy-cloud.sh ran
    setup_nats_streams.py via `docker exec brain_agent`, with the brain's
    credentials and before the brain had started. Every invocation in the
    repo's scripts, workflows and compose files must run as the provisioner."""
    import re
    import subprocess
    from pathlib import Path

    import yaml

    repo = Path(__file__).resolve().parents[2]
    hits = subprocess.run(
        [
            "git",
            "grep",
            "-n",
            "-e",
            "setup_nats_streams.py",
            "-e",
            "nats_provisioner",
            "--",
            "scripts",
            ".github",
            "docker-compose*.yml",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    offenders = []
    seen_compose = False
    for hit in hits:
        path, _, line = hit.split(":", 2)
        if "setup_nats_streams.py" not in line or line.lstrip().startswith("#"):
            continue
        if path.startswith("docker-compose"):
            services = yaml.safe_load((repo / path).read_text())["services"]
            runners = sorted(
                name
                for name, service in services.items()
                if "setup_nats_streams.py" in str(service.get("command", ""))
            )
            seen_compose = True
            ok = runners == ["nats_provisioner"]
        else:
            ok = "NATS_USER=nats_provisioner" in line
        if re.search(r"docker\s+exec", line) or not ok:
            offenders.append(hit)
    assert seen_compose, "the compose provisioner service no longer runs setup"
    assert offenders == []
