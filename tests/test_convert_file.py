"""Unit and integration tests for the Universal File Converter and Document Transformer."""

from __future__ import annotations

import base64
import csv
import json
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from jarvis.config import Config, load_config
from jarvis.perception.elements import Observation
from jarvis.tools import converter, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


def test_schema_and_registry_registration():
    """Verify convert_file is properly defined in schema and registered in handlers."""
    assert "convert_file" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["convert_file"]
    assert action.category == "files"
    assert any(p.name == "source" for p in action.params)
    assert any(p.name == "target" for p in action.params)
    assert any(p.name == "target_format" for p in action.params)
    assert any(p.name == "options" for p in action.params)


def test_markdown_to_styled_html(temp_workspace):
    """Test converting Markdown with headers, lists, code, tables, and themes into HTML."""
    md_file = temp_workspace / "sample.md"
    html_file = temp_workspace / "sample.html"

    md_content = """# Architecture Plan

Welcome to the project! Here is what we're building:

## Overview
- [x] High performance pipeline
- [ ] Database synchronization
- 1. First milestone
- 2. Second milestone

> Important: Ensure backups are taken regularly.

```python
def calculate_metrics(data: list) -> float:
    return sum(data) / len(data)
```

| Metric | Target | Status |
|---|---|---|
| Latency | < 100ms | PASS |
| Memory | < 512MB | PASS |

Check [Documentation](https://example.com) for details.
"""
    md_file.write_text(md_content, encoding="utf-8")

    res = converter.convert_file(
        source=str(md_file),
        target=str(html_file),
        options={"theme": "dark", "title": "System Architecture"},
        allow=(str(temp_workspace),),
    )
    assert "converted Markdown" in res
    assert html_file.exists()

    html_text = html_file.read_text(encoding="utf-8")
    assert "<title>System Architecture</title>" in html_text
    assert "<h1>Architecture Plan</h1>" in html_text
    assert "language-python" in html_text
    assert "calculate_metrics" in html_text
    assert "<table>" in html_text
    assert "<th>Metric</th>" in html_text
    assert "<td>PASS</td>" in html_text
    assert "<blockquote>" in html_text
    assert "--bg: #0f172a;" in html_text  # Dark theme color


def test_csv_to_json_and_json_to_csv(temp_workspace):
    """Test bidirectional CSV <-> JSON conversions with auto-typed numbers & booleans."""
    csv_file = temp_workspace / "users.csv"
    json_file = temp_workspace / "users.json"
    csv_out_file = temp_workspace / "users_roundtrip.csv"

    csv_data = "name,age,active,score\nAlice,30,true,98.5\nBob,25,false,82.0\nCharlie,35,true,100\n"
    csv_file.write_text(csv_data, encoding="utf-8")

    # CSV -> JSON
    res1 = converter.convert_file(
        source=str(csv_file),
        target=str(json_file),
        allow=(str(temp_workspace),),
    )
    assert "converted CSV" in res1
    assert json_file.exists()

    parsed = json.loads(json_file.read_text(encoding="utf-8"))
    assert len(parsed) == 3
    assert parsed[0]["name"] == "Alice"
    assert parsed[0]["age"] == 30
    assert parsed[0]["active"] is True
    assert parsed[0]["score"] == 98.5

    # JSON -> CSV
    res2 = converter.convert_file(
        source=str(json_file),
        target=str(csv_out_file),
        allow=(str(temp_workspace),),
    )
    assert "converted JSON" in res2
    assert csv_out_file.exists()

    csv_text = csv_out_file.read_text(encoding="utf-8")
    assert "name,age,active,score" in csv_text
    assert "Alice,30,True,98.5" in csv_text or "Alice,30,true,98.5" in csv_text


def test_json_to_yaml(temp_workspace):
    """Test converting JSON to YAML structure."""
    json_file = temp_workspace / "config.json"
    yaml_file = temp_workspace / "config.yaml"

    data = {
        "server": {
            "host": "127.0.0.1",
            "port": 8080,
            "ssl": False,
        },
        "tags": ["prod", "us-east"],
    }
    json_file.write_text(json.dumps(data), encoding="utf-8")

    res = converter.convert_file(
        source=str(json_file),
        target=str(yaml_file),
        allow=(str(temp_workspace),),
    )
    assert "converted JSON" in res
    assert yaml_file.exists()

    yaml_text = yaml_file.read_text(encoding="utf-8")
    assert "server:" in yaml_text
    assert "host: 127.0.0.1" in yaml_text or "host: \"127.0.0.1\"" in yaml_text
    assert "port: 8080" in yaml_text


def test_csv_to_xlsx(temp_workspace):
    """Test generating a valid OOXML Excel workbook from CSV."""
    csv_file = temp_workspace / "sales.csv"
    xlsx_file = temp_workspace / "sales.xlsx"

    csv_content = "Product,Q1,Q2,Total\nWidget A,150,200,350\nWidget B,80,120,200\n"
    csv_file.write_text(csv_content, encoding="utf-8")

    res = converter.convert_file(
        source=str(csv_file),
        target=str(xlsx_file),
        allow=(str(temp_workspace),),
    )
    assert "Excel" in res
    assert xlsx_file.exists()
    assert xlsx_file.stat().st_size > 500  # Valid zip file


def test_image_format_conversion_and_resizing(temp_workspace):
    """Test image format transformation (PNG -> JPEG / WEBP), resizing, and grayscale."""
    png_file = temp_workspace / "test_image.png"
    jpg_file = temp_workspace / "test_image.jpg"
    webp_file = temp_workspace / "test_thumb.webp"

    # Create dummy RGB image
    img = Image.new("RGBA", (400, 300), color=(100, 150, 200, 255))
    img.save(png_file, format="PNG")

    # Convert to JPEG with resize
    res1 = converter.convert_file(
        source=str(png_file),
        target=str(jpg_file),
        options={"resize_width": 200, "resize_height": 150, "quality": 90},
        allow=(str(temp_workspace),),
    )
    assert "converted image" in res1
    assert jpg_file.exists()
    with Image.open(jpg_file) as j_img:
        assert j_img.size == (200, 150)
        assert j_img.format == "JPEG"

    # Convert to WEBP with aspect-ratio proportional resize
    res2 = converter.convert_file(
        source=str(png_file),
        target=str(webp_file),
        options={"resize_width": 200},
        allow=(str(temp_workspace),),
    )
    assert "converted image" in res2
    assert webp_file.exists()
    with Image.open(webp_file) as w_img:
        assert w_img.size == (200, 150)
        assert w_img.format == "WEBP"

    # Convert to grayscale PNG
    gray_file = temp_workspace / "test_gray.png"
    res3 = converter.convert_file(
        source=str(png_file),
        target=str(gray_file),
        options={"grayscale": True},
        allow=(str(temp_workspace),),
    )
    assert "converted image" in res3
    assert gray_file.exists()
    with Image.open(gray_file) as g_img:
        assert g_img.mode == "L"


def test_base64_encode_and_decode(temp_workspace):
    """Test encoding files to base64 and decoding back to binary/text."""
    src_file = temp_workspace / "secret.txt"
    b64_file = temp_workspace / "secret.b64"
    decoded_file = temp_workspace / "secret_restored.txt"

    original_bytes = b"JARVIS Autonomous System Token 9876543210"
    src_file.write_bytes(original_bytes)

    # Encode to Base64
    res_enc = converter.convert_file(
        source=str(src_file),
        target=str(b64_file),
        target_format="base64",
        allow=(str(temp_workspace),),
    )
    assert "encoded" in res_enc
    assert b64_file.exists()

    b64_str = b64_file.read_text(encoding="utf-8").strip()
    assert base64.b64encode(original_bytes).decode("ascii") == b64_str

    # Decode from Base64
    res_dec = converter.convert_file(
        source=str(b64_file),
        target=str(decoded_file),
        allow=(str(temp_workspace),),
    )
    assert "decoded" in res_dec
    assert decoded_file.exists()
    assert decoded_file.read_bytes() == original_bytes


def test_encoding_and_newline_normalization(temp_workspace):
    """Test CRLF / LF line ending normalization."""
    file_crlf = temp_workspace / "crlf.txt"
    file_lf = temp_workspace / "lf.txt"

    file_crlf.write_bytes(b"line 1\r\nline 2\r\nline 3\r\n")

    res = converter.convert_file(
        source=str(file_crlf),
        target=str(file_lf),
        target_format="lf",
        allow=(str(temp_workspace),),
    )
    assert "normalized" in res
    assert file_lf.read_bytes() == b"line 1\nline 2\nline 3\n"


def test_registry_execution_and_sandbox(temp_workspace):
    """Test executing convert_file through registry.execute with sandbox enforcement."""
    cfg = load_config()
    cfg.safety.allow_paths = (str(temp_workspace),)

    src = temp_workspace / "doc.md"
    tgt = temp_workspace / "doc.html"
    src.write_text("# Hello World", encoding="utf-8")

    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Desktop",
    )

    action_result = registry.execute(
        name="convert_file",
        args={"source": str(src), "target": str(tgt), "options": {"theme": "cyber"}},
        obs=obs,
        cfg=cfg,
    )

    assert action_result.ok is True
    assert "converted Markdown" in action_result.message
    assert tgt.exists()
    assert "#00f0ff" in tgt.read_text(encoding="utf-8")  # Cyber theme color
