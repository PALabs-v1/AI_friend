import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.bootstrap import env_wizard


def test_init_wizard_generates_every_mesh_password(monkeypatch, tmp_path):
    generated = 0

    def secure_password(length=24):
        nonlocal generated
        generated += 1
        return f"P{generated:02d}" + "x" * (length - 3)

    monkeypatch.setattr(env_wizard, "generate_secure_password", secure_password)
    monkeypatch.setattr(
        env_wizard,
        "prompt_user",
        lambda _question, default="", choices=None: (
            default
            if default and (choices is None or default in choices)
            else choices[0]
            if choices
            else "friend"
        ),
    )
    monkeypatch.setattr(
        env_wizard,
        "prompt_secret",
        lambda _question, default="": default or secure_password(),
    )
    target = tmp_path / ".env"

    assert env_wizard.run_init_wizard(target) == 0

    env_values = dict(
        line.split("=", 1)
        for line in target.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    roles = (
        "PROVISIONER",
        "SIGNALING",
        "BRAIN",
        "SUBCONSCIOUS",
        "SURFACING",
        "SYSTEM",
        "TRANSPORT",
        "STT",
        "VISION",
        "VOICE",
    )
    passwords = [env_values[f"NATS_{role}_PASSWORD"] for role in roles]
    assert all(len(password) == 32 for password in passwords)
    assert len(set(passwords)) == len(roles)
