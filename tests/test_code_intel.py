"""Unit and integration tests for Codebase AST and Semantic Symbol Intelligence Engine."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import code_intel, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def temp_project():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Create a sample Python module
        py_file = root / "models.py"
        py_file.write_text('''"""Database and entity models module."""

from __future__ import annotations
import os
import sqlite3
from typing import Optional, List, Dict, Any


GLOBAL_FLAG: bool = True


class BaseModel:
    """Base persistence model."""
    def __init__(self, id: int = 0):
        self.id = id

    def save(self) -> bool:
        """Persist model to storage."""
        return True


class User(BaseModel):
    """User account representation."""
    def __init__(self, username: str, email: str, role: str = "user"):
        super().__init__()
        self.username = username
        self.email = email
        self.role = role

    async def fetch_permissions(self, refresh: bool = False) -> List[str]:
        """Fetch list of user permission strings."""
        return ["read", "write"]


def calculate_hash(data: str, salt: Optional[str] = None) -> str:
    """Calculate secure payload hash."""
    return f"hash_{data}_{salt}"


async def background_sync(users: List[User]) -> int:
    """Sync all active users."""
    return len(users)
''', encoding="utf-8")

        # Create a sample JS file
        js_file = root / "app.js"
        js_file.write_text('''import React from 'react';
import { useState, useEffect } from 'react';

export class AppController {
    constructor() {}
}

export function renderHeader(title) {
    return `<h1>${title}</h1>`;
}

export const fetchConfig = async (endpoint) => {
    return { status: 200 };
};
''', encoding="utf-8")

        yield root


def test_schema_and_registry_registration():
    """Verify code_intel is registered in schema and handlers."""
    assert "code_intel" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["code_intel"]
    assert action.category == "coding"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "path" for p in action.params)
    assert any(p.name == "query" for p in action.params)


def test_parse_python_ast_symbols(temp_project):
    """Test AST parsing of Python classes, methods, async functions, arguments, and docstrings."""
    py_path = temp_project / "models.py"
    res = code_intel.code_intel(op="symbols", path=str(py_path))

    assert "File: models.py" in res
    assert "Database and entity models module." in res
    assert "class BaseModel" in res
    assert "class User(BaseModel)" in res
    assert "save(self) -> bool" in res
    assert "async fetch_permissions(self, refresh: bool = False) -> List[str]" in res
    assert "calculate_hash(data: str, salt: Optional[str] = None) -> str" in res
    assert "async background_sync(users: List[User]) -> int" in res


def test_parse_js_symbols(temp_project):
    """Test symbol parsing on JavaScript files."""
    js_path = temp_project / "app.js"
    res = code_intel.code_intel(op="symbols", path=str(js_path))

    assert "File: app.js" in res
    assert "class AppController" in res
    assert "renderHeader(title)" in res
    assert "fetchConfig(endpoint)" in res


def test_search_symbols_across_project(temp_project):
    """Test finding symbol definitions across directory."""
    res = code_intel.code_intel(op="search", path=str(temp_project), query="User")
    assert "found" in res
    assert "class User" in res
    assert "models.py" in res

    res_fn = code_intel.code_intel(op="search", path=str(temp_project), query="calculate_hash")
    assert "function calculate_hash()" in res_fn


def test_dependency_analysis(temp_project):
    """Test extracting imported dependencies."""
    res = code_intel.code_intel(op="dependencies", path=str(temp_project))
    assert "Dependency Analysis" in res
    assert "sqlite3" in res or "os" in res


def test_codebase_summary_tree(temp_project):
    """Test structural summary generation."""
    res = code_intel.code_intel(op="summary", path=str(temp_project))
    assert "Codebase Structure" in res
    assert "models.py" in res
    assert "app.js" in res


def test_registry_execution(temp_project):
    """Test executing code_intel through the registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="IDE",
    )
    result = registry.execute(
        name="code_intel",
        args={"op": "symbols", "path": str(temp_project / "models.py")},
        obs=obs,
        cfg=cfg,
    )
    assert result.ok is True
    assert "class User" in result.message
    assert result.needs_observe is False
