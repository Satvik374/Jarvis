"""Unit and integration tests for JSON Schema & Data Validation Engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import data_validate, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def user_schema():
    return {
        "type": "object",
        "required": ["id", "username", "email", "age", "role"],
        "properties": {
            "id": {"type": "integer", "minimum": 1},
            "username": {"type": "string", "minLength": 3, "maxLength": 20},
            "email": {"type": "string", "pattern": r"^[\w\.-]+@[\w\.-]+\.\w+$"},
            "age": {"type": "integer", "minimum": 18, "maximum": 120},
            "role": {"type": "string", "enum": ["admin", "editor", "viewer"]},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }


def test_schema_and_registry_registration():
    """Verify data_validate is in schema and registered in handlers."""
    assert "data_validate" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["data_validate"]
    assert action.category == "coding"
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "data" for p in action.params)
    assert any(p.name == "schema" for p in action.params)


def test_validation_success(user_schema):
    """Test valid data passing schema validation."""
    valid_user = {
        "id": 101,
        "username": "alex_dev",
        "email": "alex@example.com",
        "age": 28,
        "role": "admin",
        "tags": ["python", "ai"],
    }
    res = data_validate.data_validate(
        op="validate",
        data=json.dumps(valid_user),
        schema=json.dumps(user_schema),
    )
    data = json.loads(res)
    assert data["status"] == "VALID"


def test_validation_errors(user_schema):
    """Test invalid data returning detailed error messages."""
    invalid_user = {
        "id": 0,  # Below minimum 1
        "username": "al",  # Below minLength 3
        "email": "bad-email-string",  # Fails pattern
        "age": 15,  # Below minimum 18
        "role": "superadmin",  # Not in enum
    }
    res = data_validate.data_validate(
        op="validate",
        data=invalid_user,
        schema=user_schema,
    )
    data = json.loads(res)
    assert data["status"] == "INVALID"
    assert data["error_count"] >= 4
    error_str = " ".join(data["errors"])
    assert "minimum" in error_str
    assert "minLength" in error_str
    assert "pattern" in error_str
    assert "enum" in error_str


def test_schema_inference():
    """Test inferring JSON Schema from sample object."""
    sample = {
        "title": "Quantum AI",
        "version": 2,
        "active": True,
        "score": 98.5,
        "authors": ["Alice", "Bob"],
        "metadata": {
            "views": 500,
        },
    }
    res = data_validate.data_validate(op="infer", data=sample)
    schema = json.loads(res)

    assert schema["type"] == "object"
    assert schema["properties"]["title"]["type"] == "string"
    assert schema["properties"]["version"]["type"] == "integer"
    assert schema["properties"]["active"]["type"] == "boolean"
    assert schema["properties"]["score"]["type"] == "number"
    assert schema["properties"]["authors"]["type"] == "array"
    assert schema["properties"]["metadata"]["type"] == "object"


def test_deep_diff():
    """Test computing deep structural difference."""
    obj_a = {
        "app": "Jarvis",
        "version": "1.0.0",
        "settings": {"debug": True, "port": 8080},
        "flags": ["alpha", "beta"],
    }
    obj_b = {
        "app": "Jarvis",
        "version": "1.1.0",
        "settings": {"debug": False, "port": 8080, "timeout": 30},
        "flags": ["alpha", "gamma"],
    }
    res = data_validate.data_validate(op="diff", data=obj_a, target=obj_b)
    data = json.loads(res)

    assert data["status"] == "OK"
    assert data["diff_count"] >= 3
    paths = [d["path"] for d in data["diffs"]]
    assert "$.version" in paths
    assert "$.settings.debug" in paths
    assert "$.settings.timeout" in paths


def test_data_sanitization():
    """Test whitespace trimming and type casting."""
    raw = {
        "username": "  alice   ",
        "age": "25",
        "tags": ["  ai  ", " coding "],
    }
    schema = {
        "type": "object",
        "properties": {
            "username": {"type": "string"},
            "age": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
    res = data_validate.data_validate(op="sanitize", data=raw, schema=schema)
    cleaned = json.loads(res)

    assert cleaned["username"] == "alice"
    assert cleaned["age"] == 25
    assert cleaned["tags"] == ["ai", "coding"]


def test_registry_execution(user_schema):
    """Test executing data_validate through registry dispatcher."""
    valid_user = {
        "id": 1,
        "username": "admin_user",
        "email": "admin@jarvis.ai",
        "age": 30,
        "role": "admin",
    }
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Code",
    )

    res = registry.execute(
        name="data_validate",
        args={"op": "validate", "data": valid_user, "schema": user_schema},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert '"status": "VALID"' in res.message
    assert res.needs_observe is False
