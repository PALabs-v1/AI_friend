from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tools.repo_intel.db import connect


def git(root: Path, *args: str) -> None:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, env=env
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small repository covering the awkward cases: odd names, symlink, binary, untracked, ignored."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text(
        "def handler(event):\n    return event['value'] * 2\n\nclass Router:\n    pass\n"
    )
    (root / "src" / "util.py").write_text(
        "def helper_function_name():\n    return 'literal_marker_text'\n"
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(
        "def test_handler():\n    assert True\n"
    )
    (root / "README.md").write_text(
        "# Title\n\nSome documentation text for the readme file here.\n"
    )
    (root / "with space.md").write_text(
        "spaced file name content for the inventory test\n"
    )
    (root / "-leading-dash.txt").write_text(
        "leading dash file name content for the inventory\n"
    )
    (root / "new\nline.txt").write_text(
        "newline inside the file name content for the test\n"
    )
    (root / "blob.bin").write_bytes(b"\x00\x01\x02binary payload")
    (root / "empty.py").write_text("")
    (root / "alias.md").symlink_to("README.md")
    (root / ".gitignore").write_text("ignored_dir/\n*.log\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    (root / "ignored_dir").mkdir()
    (root / "ignored_dir" / "secret.env").write_text("TOKEN=do-not-read-me\n")
    (root / "run.log").write_text("log\n")
    (root / "untracked.py").write_text(
        "def brand_new_untracked_function():\n    return 1\n"
    )
    return root


@pytest.fixture
def conn(tmp_path: Path):
    return connect(tmp_path / "index.db")
