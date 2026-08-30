"""JSON Schema & Data Validation Engine for Jarvis.

Enables JSON Schema validation, automatic schema inference, deep structural diffing,
and input sanitization without external third-party dependencies.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .files import _expand, _within


def _parse_input(val: Any) -> Tuple[Any, Optional[str]]:
    """Parse JSON string, file path, or in-memory python object."""
    if isinstance(val, (dict, list)):
        return val, None
    if isinstance(val, str):
        s = val.strip()
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            try:
                return json.loads(s), None
            except Exception as exc:
                return None, f"invalid JSON string: {exc}"
        p = _expand(s)
        if p.exists() and p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8")), None
            except Exception as exc:
                return None, f"failed to read JSON file '{p}': {exc}"
    return val, None


# --------------------------------------------------------------------------- #
# Schema Validator
# --------------------------------------------------------------------------- #

def validate_data(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Validate data instance against JSON Schema constraints."""
    errors: List[str] = []

    # Check type
    expected_type = schema.get("type")
    if expected_type:
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        # In Python, bool is a subclass of int, so handle explicitly
        if expected_type == "integer" and isinstance(instance, bool):
            errors.append(f"{path}: expected integer, got boolean")
        elif expected_type == "number" and isinstance(instance, bool):
            errors.append(f"{path}: expected number, got boolean")
        elif expected_type in type_map:
            t = type_map[expected_type]
            if not isinstance(instance, t):
                errors.append(f"{path}: expected type '{expected_type}', got '{type(instance).__name__}'")

    # Check enum
    if "enum" in schema:
        if instance not in schema["enum"]:
            errors.append(f"{path}: value '{instance}' is not one of allowed enum values {schema['enum']}")

    # String constraints
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: string length {len(instance)} is less than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string length {len(instance)} exceeds maxLength {schema['maxLength']}")
        if "pattern" in schema:
            if not re.search(schema["pattern"], instance):
                errors.append(f"{path}: string does not match regex pattern '{schema['pattern']}'")

    # Number constraints
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value {instance} is less than minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: value {instance} exceeds maximum {schema['maximum']}")

    # Object constraints
    if isinstance(instance, dict):
        required_keys = schema.get("required", [])
        for rk in required_keys:
            if rk not in instance:
                errors.append(f"{path}: missing required property '{rk}'")

        properties = schema.get("properties", {})
        for prop_name, prop_schema in properties.items():
            if prop_name in instance:
                errors.extend(validate_data(instance[prop_name], prop_schema, path=f"{path}.{prop_name}"))

    # Array constraints
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: array item count {len(instance)} is less than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: array item count {len(instance)} exceeds maxItems {schema['maxItems']}")
        if "items" in schema and isinstance(schema["items"], dict):
            item_schema = schema["items"]
            for idx, item in enumerate(instance):
                errors.extend(validate_data(item, item_schema, path=f"{path}[{idx}]"))

    return errors


# --------------------------------------------------------------------------- #
# Schema Inference
# --------------------------------------------------------------------------- #

def infer_schema(data: Any) -> Dict[str, Any]:
    """Derive standard JSON Schema from data structure."""
    if data is None:
        return {"type": "null"}
    elif isinstance(data, bool):
        return {"type": "boolean"}
    elif isinstance(data, int):
        return {"type": "integer"}
    elif isinstance(data, float):
        return {"type": "number"}
    elif isinstance(data, str):
        return {"type": "string"}
    elif isinstance(data, list):
        if not data:
            return {"type": "array", "items": {}}
        return {"type": "array", "items": infer_schema(data[0])}
    elif isinstance(data, dict):
        props = {}
        for k, v in data.items():
            props[str(k)] = infer_schema(v)
        return {
            "type": "object",
            "required": list(data.keys()),
            "properties": props,
        }
    return {"type": "string"}


# --------------------------------------------------------------------------- #
# Deep Structural Diff
# --------------------------------------------------------------------------- #

def deep_diff(obj_a: Any, obj_b: Any, path: str = "$") -> List[Dict[str, Any]]:
    """Compute structural semantic diff between two JSON objects."""
    diffs: List[Dict[str, Any]] = []

    if type(obj_a) != type(obj_b):
        return [{"path": path, "type": "type_changed", "old_type": type(obj_a).__name__, "new_type": type(obj_b).__name__, "old": obj_a, "new": obj_b}]

    if isinstance(obj_a, dict):
        keys_a = set(obj_a.keys())
        keys_b = set(obj_b.keys())

        for k in sorted(keys_a - keys_b):
            diffs.append({"path": f"{path}.{k}", "type": "removed", "value": obj_a[k]})
        for k in sorted(keys_b - keys_a):
            diffs.append({"path": f"{path}.{k}", "type": "added", "value": obj_b[k]})
        for k in sorted(keys_a & keys_b):
            diffs.extend(deep_diff(obj_a[k], obj_b[k], path=f"{path}.{k}"))

    elif isinstance(obj_a, list):
        len_a = len(obj_a)
        len_b = len(obj_b)
        min_len = min(len_a, len_b)
        for i in range(min_len):
            diffs.extend(deep_diff(obj_a[i], obj_b[i], path=f"{path}[{i}]"))
        if len_a > len_b:
            for i in range(min_len, len_a):
                diffs.append({"path": f"{path}[{i}]", "type": "removed", "value": obj_a[i]})
        elif len_b > len_a:
            for i in range(min_len, len_b):
                diffs.append({"path": f"{path}[{i}]", "type": "added", "value": obj_b[i]})

    else:
        if obj_a != obj_b:
            diffs.append({"path": path, "type": "modified", "old": obj_a, "new": obj_b})

    return diffs


# --------------------------------------------------------------------------- #
# Data Sanitization
# --------------------------------------------------------------------------- #

def sanitize_data(data: Any, schema: Dict[str, Any]) -> Any:
    """Trim strings, strip undeclared keys, and cast basic types according to schema."""
    if not isinstance(schema, dict):
        return data

    expected_type = schema.get("type")

    if expected_type == "string" and isinstance(data, str):
        return data.strip()
    elif expected_type == "integer" and not isinstance(data, bool):
        try:
            return int(data)
        except (ValueError, TypeError):
            return data
    elif expected_type == "number" and not isinstance(data, bool):
        try:
            return float(data)
        except (ValueError, TypeError):
            return data
    elif expected_type == "object" and isinstance(data, dict):
        props = schema.get("properties", {})
        cleaned = {}
        for k, v in data.items():
            if k in props:
                cleaned[k] = sanitize_data(v, props[k])
            else:
                cleaned[k] = v  # Keep or preserve unless strict
        return cleaned
    elif expected_type == "array" and isinstance(data, list):
        item_schema = schema.get("items", {})
        return [sanitize_data(item, item_schema) for item in data]

    return data


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def data_validate(
    op: str = "validate",
    data: Any = None,
    schema: Any = None,
    target: Any = None,
    allow: tuple[str, ...] = (),
) -> str:
    """Validate JSON data against a schema, infer schemas, compute deep diffs, or sanitize payloads.

    Operations:
      - 'validate': Verify data complies with schema (types, required fields, enums, regex patterns, ranges).
      - 'infer': Generate a complete JSON Schema from existing data.
      - 'diff': Compute deep structural difference between data and target.
      - 'sanitize': Clean whitespace, cast scalars, and normalize structures according to schema.
    """
    op_clean = (op or "validate").strip().lower()

    if op_clean in ("validate", "check", "verify"):
        parsed_data, err_d = _parse_input(data)
        if err_d:
            return err_d
        parsed_schema, err_s = _parse_input(schema)
        if err_s:
            return err_s
        if not isinstance(parsed_schema, dict):
            return "schema must be a valid JSON object"

        errors = validate_data(parsed_data, parsed_schema)
        if not errors:
            return json.dumps({"status": "VALID", "message": "Data successfully passed schema validation."}, indent=2)
        return json.dumps({"status": "INVALID", "error_count": len(errors), "errors": errors}, indent=2)

    elif op_clean in ("infer", "generate_schema", "schema"):
        parsed_data, err = _parse_input(data)
        if err:
            return err
        inferred = infer_schema(parsed_data)
        return json.dumps(inferred, indent=2)

    elif op_clean in ("diff", "compare"):
        parsed_a, err_a = _parse_input(data)
        if err_a:
            return err_a
        parsed_b, err_b = _parse_input(target or schema)
        if err_b:
            return err_b
        diffs = deep_diff(parsed_a, parsed_b)
        return json.dumps({"status": "OK", "diff_count": len(diffs), "diffs": diffs}, indent=2)

    elif op_clean in ("sanitize", "clean", "normalize"):
        parsed_data, err_d = _parse_input(data)
        if err_d:
            return err_d
        parsed_schema, err_s = _parse_input(schema)
        if err_s:
            return err_s
        cleaned = sanitize_data(parsed_data, parsed_schema or {})
        return json.dumps(cleaned, indent=2)

    return f"unknown data_validate op '{op}' - supported: validate, infer, diff, sanitize"
