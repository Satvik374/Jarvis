"""Unit and integration tests for Text Diff & Unified Patch Engine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import diff_patch, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def sample_files():
    with tempfile.TemporaryDirectory() as td:
        dir_p = Path(td)
        f_v1 = dir_p / "v1.py"
        f_v2 = dir_p / "v2.py"

        f_v1.write_text("def hello():\n    print('hello world')\n    return 1\n", encoding="utf-8")
        f_v2.write_text("def hello():\n    print('hello Jarvis')\n    return 2\n", encoding="utf-8")

        yield f_v1, f_v2


def test_schema_and_registry_registration():
    """Verify diff_patch is in schema and registered in handlers."""
    assert "diff_patch" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["diff_patch"]
    assert action.category == "coding"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "source" for p in action.params)
    assert any(p.name == "target" for p in action.params)


def test_unified_diff(sample_files):
    """Test generating unified diff between two files."""
    f_v1, f_v2 = sample_files
    res = diff_patch.diff_patch(op="diff", source=str(f_v1), target=str(f_v2))

    assert "--- v1.py" in res
    assert "+++ v2.py" in res
    assert "-    print('hello world')" in res
    assert "+    print('hello Jarvis')" in res


def test_similarity_computation(sample_files):
    """Test calculating sequence similarity ratio."""
    f_v1, f_v2 = sample_files
    res = diff_patch.diff_patch(op="similarity", source=str(f_v1), target=str(f_v2))
    data = json.loads(res)

    assert data["similarity_ratio"] > 0.7
    assert data["total_matched_chars"] > 20


def test_stats_computation(sample_files):
    """Test calculating change statistics."""
    f_v1, f_v2 = sample_files
    res = diff_patch.diff_patch(op="stats", source=str(f_v1), target=str(f_v2))
    data = json.loads(res)

    assert data["lines_modified"] >= 1 or (data["lines_added"] >= 1 and data["lines_deleted"] >= 1)
    assert data["lines_unchanged"] >= 1


def test_patch_dry_run_and_apply(tmp_path):
    """Test dry-run simulation and applying unified patch."""
    target_f = tmp_path / "app.py"
    target_f.write_text("def greet():\n    print('old text')\n", encoding="utf-8")

    patch_text = "--- app.py\n+++ app.py\n@@ -1,2 +1,2 @@\n def greet():\n-    print('old text')\n+    print('new text')\n"

    # Dry-run
    res_dry = diff_patch.diff_patch(
        op="patch",
        target=str(target_f),
        patch_text=patch_text,
        dry_run=True,
    )
    assert "dry-run successful" in res_dry
    assert "old text" in target_f.read_text(encoding="utf-8")

    # Real apply
    res_apply = diff_patch.diff_patch(
        op="patch",
        target=str(target_f),
        patch_text=patch_text,
        dry_run=False,
    )
    assert "patch applied successfully" in res_apply
    assert "new text" in target_f.read_text(encoding="utf-8")


def test_registry_execution(sample_files):
    """Test executing diff_patch through registry dispatcher."""
    f_v1, f_v2 = sample_files
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Diff",
    )

    res = registry.execute(
        name="diff_patch",
        args={"op": "similarity", "source": str(f_v1), "target": str(f_v2)},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert '"similarity_ratio":' in res.message
    assert res.needs_observe is False
