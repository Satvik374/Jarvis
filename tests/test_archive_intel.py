"""Unit and integration tests for Archive & Compression Intelligence Engine."""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import archive_intel, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def sample_folder_tree():
    """Generate a temporary directory with nested files for compression tests."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "project"
        root.mkdir()
        (root / "README.md").write_text("# Project Alpha\nDocumentation line.", encoding="utf-8")
        (root / "main.py").write_text("print('hello world')", encoding="utf-8")
        sub = root / "src"
        sub.mkdir()
        (sub / "utils.py").write_text("def add(a, b): return a + b", encoding="utf-8")

        yield root


def test_schema_and_registry_registration():
    """Verify archive_intel is in schema and registered in handlers."""
    assert "archive_intel" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["archive_intel"]
    assert action.category == "files"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "path" for p in action.params)
    assert any(p.name == "target" for p in action.params)


def test_zip_create_list_and_test(sample_folder_tree):
    """Test packing a directory to zip, listing contents, and verifying integrity."""
    zip_out = sample_folder_tree.parent / "bundle.zip"
    res_create = archive_intel.archive_intel(
        path=str(sample_folder_tree),
        target=str(zip_out),
        op="create",
        archive_format="zip",
    )
    assert "created archive" in res_create
    assert zip_out.exists()

    # List entries
    res_list = archive_intel.archive_intel(path=str(zip_out), op="list")
    data = json.loads(res_list)
    assert data["total_files"] >= 3
    filenames = [e["filename"] for e in data["entries"]]
    assert any("README.md" in fn for fn in filenames)
    assert any("utils.py" in fn for fn in filenames)

    # Test integrity
    res_test = archive_intel.archive_intel(path=str(zip_out), op="test")
    test_data = json.loads(res_test)
    assert test_data["status"] == "OK"


def test_zip_extract(sample_folder_tree):
    """Test extracting a zip archive."""
    zip_out = sample_folder_tree.parent / "bundle.zip"
    archive_intel.archive_intel(path=str(sample_folder_tree), target=str(zip_out), op="create")

    dest_dir = sample_folder_tree.parent / "unpacked"
    res_extract = archive_intel.archive_intel(
        path=str(zip_out),
        target=str(dest_dir),
        op="extract",
    )
    assert "extracted" in res_extract
    assert (dest_dir / "README.md").exists()
    assert (dest_dir / "src" / "utils.py").exists()


def test_targz_create_and_extract(sample_folder_tree):
    """Test tar.gz archive creation and extraction."""
    tar_out = sample_folder_tree.parent / "bundle.tar.gz"
    res_create = archive_intel.archive_intel(
        path=str(sample_folder_tree),
        target=str(tar_out),
        op="create",
        archive_format="tar.gz",
    )
    assert "created archive" in res_create
    assert tar_out.exists()

    dest_dir = sample_folder_tree.parent / "tar_unpacked"
    res_extract = archive_intel.archive_intel(
        path=str(tar_out),
        target=str(dest_dir),
        op="extract",
    )
    assert "extracted" in res_extract
    assert (dest_dir / "README.md").exists()


def test_zip_slip_security_prevention(tmp_path):
    """Test that Zip-Slip malicious path traversal filenames are rejected."""
    bad_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(str(bad_zip), "w") as zf:
        zf.writestr("../../evil.txt", "malicious payload")

    extract_dest = tmp_path / "safe_dir"
    res = archive_intel.archive_intel(
        path=str(bad_zip),
        target=str(extract_dest),
        op="extract",
    )
    assert "security violation" in res or "path traversal" in res
    assert not (tmp_path / "evil.txt").exists()


def test_zip_slip_rejects_sibling_with_shared_prefix(tmp_path):
    destination = tmp_path / "output"
    assert archive_intel._is_zip_slip_safe(destination, "nested/file.txt")
    assert not archive_intel._is_zip_slip_safe(destination, "../output-other/file.txt")
    bad_zip = tmp_path / "sibling.zip"
    with zipfile.ZipFile(bad_zip, "w") as archive:
        archive.writestr("../output-other/file.txt", "test payload")
    assert "security violation" in archive_intel.extract_archive(bad_zip, destination)


def test_registry_execution(sample_folder_tree):
    """Test executing archive_intel through registry dispatcher."""
    zip_out = sample_folder_tree.parent / "registry_bundle.zip"
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Files",
    )

    res = registry.execute(
        name="archive_intel",
        args={"path": str(sample_folder_tree), "target": str(zip_out), "op": "create"},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert "created archive" in res.message
    assert res.needs_observe is False
