"""Unit and integration tests for Git & Version Control Intelligence Engine."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import git_intel, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def temp_git_repo():
    with tempfile.TemporaryDirectory() as td:
        repo_dir = Path(td)
        # Initialize repo
        subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "JarvisTest"], cwd=str(repo_dir), capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@jarvis.ai"], cwd=str(repo_dir), capture_output=True, text=True)

        # Create README
        (repo_dir / "README.md").write_text("# Test Repo\nInitial content.", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_dir), capture_output=True, text=True)

        yield repo_dir


def test_schema_and_registry_registration():
    """Verify git_intel is in schema and registered in handlers."""
    assert "git_intel" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["git_intel"]
    assert action.category == "coding"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "path" for p in action.params)
    assert any(p.name == "message" for p in action.params)


def test_git_status_clean_and_modified(temp_git_repo):
    """Test git status on clean and modified repo."""
    res_clean = git_intel.git_intel(path=str(temp_git_repo), op="status")
    assert "Branch" in res_clean

    # Add a new file and modify existing
    (temp_git_repo / "main.py").write_text("print('hello')", encoding="utf-8")
    (temp_git_repo / "README.md").write_text("# Updated Title", encoding="utf-8")

    res_dirty = git_intel.git_intel(path=str(temp_git_repo), op="status")
    assert "README.md" in res_dirty
    assert "main.py" in res_dirty


def test_git_commit_and_log(temp_git_repo):
    """Test creating a commit and reading log history."""
    (temp_git_repo / "app.py").write_text("def run(): pass", encoding="utf-8")

    res_commit = git_intel.git_intel(path=str(temp_git_repo), op="commit", message="feat: add app runner")
    assert "commit" in res_commit or "OK" in res_commit

    res_log = git_intel.git_intel(path=str(temp_git_repo), op="log", limit=5)
    assert "feat: add app runner" in res_log
    assert "Initial commit" in res_log


def test_git_diff(temp_git_repo):
    """Test inspecting unified diffs."""
    (temp_git_repo / "README.md").write_text("# Modified Header\nNew line.", encoding="utf-8")

    res_diff = git_intel.git_intel(path=str(temp_git_repo), op="diff")
    assert "+# Modified Header" in res_diff
    assert "-# Test Repo" in res_diff


def test_git_branches(temp_git_repo):
    """Test listing branches."""
    subprocess.run(["git", "branch", "feature-x"], cwd=str(temp_git_repo), capture_output=True, text=True)

    res_branches = git_intel.git_intel(path=str(temp_git_repo), op="branches")
    assert "feature-x" in res_branches


def test_registry_execution(temp_git_repo):
    """Test executing git_intel via registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Code",
    )

    res = registry.execute(
        name="git_intel",
        args={"path": str(temp_git_repo), "op": "log", "limit": 2},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert "Initial commit" in res.message
    assert res.needs_observe is False
