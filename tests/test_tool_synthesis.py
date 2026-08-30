"""Tests for Dynamic Tool Synthesis, Sandboxed Execution, and Memory Recall."""

import json
from pathlib import Path

import pytest
from jarvis.config import Config
from jarvis.perception.elements import Observation
from jarvis.tools import registry
from jarvis.tools.tool_synthesis import (
    SynthesizedToolManager,
    get_tool_manager,
    synthesized_tools_prompt_note,
)


def _obs():
    return Observation(active_window="test", elements=[], screen_size=(1920, 1080))


def test_synthesize_valid_tool(tmp_path):
    mgr = SynthesizedToolManager(tools_dir=tmp_path / "tools")
    code = (
        "def run(num1, num2):\n"
        "    return {'sum': num1 + num2, 'product': num1 * num2}\n"
    )
    ok, msg = mgr.synthesize(
        name="math_calculator",
        description="Calculate sum and product of two numbers",
        code=code,
        parameters={"num1": {"type": "int"}, "num2": {"type": "int"}},
        tags=["math", "arithmetic"],
        test_args={"num1": 10, "num2": 5},
    )
    assert ok is True
    assert "math_calculator" in msg
    assert (tmp_path / "tools" / "math_calculator.py").exists()
    assert (tmp_path / "tools" / "tools_registry.json").exists()


def test_synthesize_rejects_syntax_error(tmp_path):
    mgr = SynthesizedToolManager(tools_dir=tmp_path / "tools")
    bad_code = "def run(:\n  invalid python"
    ok, msg = mgr.synthesize(
        name="bad_tool",
        description="Fails syntax",
        code=bad_code,
    )
    assert ok is False
    assert "SyntaxError" in msg
    assert not (tmp_path / "tools" / "bad_tool.py").exists()


def test_execute_synthesized_tool(tmp_path):
    mgr = SynthesizedToolManager(tools_dir=tmp_path / "tools")
    code = (
        "def run(text):\n"
        "    return f'REVERSED: {text[::-1]}'\n"
    )
    ok, msg = mgr.synthesize(
        name="string_reverser",
        description="Reverse a string input",
        code=code,
    )
    assert ok is True

    exec_ok, out = mgr.execute(name="string_reverser", args={"text": "Jarvis AI"})
    assert exec_ok is True
    assert "REVERSED: IA sivraJ" in out


def test_tool_matching_and_recall(tmp_path):
    mgr = SynthesizedToolManager(tools_dir=tmp_path / "tools")
    mgr.synthesize(
        name="image_resizer",
        description="Batch resize image files in a directory",
        code="def run(folder): return f'resized {folder}'",
        tags=["images", "photos", "resize"],
    )
    mgr.synthesize(
        name="audio_normalizer",
        description="Normalize loudness of wav audio files",
        code="def run(audio_file): return f'normalized {audio_file}'",
        tags=["audio", "sound"],
    )

    matches = mgr.find_matching_tools("Please resize all images in my downloads")
    assert len(matches) >= 1
    assert matches[0].name == "image_resizer"

    audio_matches = mgr.find_matching_tools("Normalize the sound level in sample.wav")
    assert len(audio_matches) >= 1
    assert audio_matches[0].name == "audio_normalizer"


def test_registry_dispatch(tmp_path, monkeypatch):
    mgr = SynthesizedToolManager(tools_dir=tmp_path / "tools")
    monkeypatch.setattr("jarvis.tools.tool_synthesis.get_tool_manager", lambda: mgr)

    cfg = Config()
    obs = _obs()

    # 1. Synthesize action
    code = "def run(x):\n    return x * 10\n"
    res_synth = registry.execute(
        "synthesize_tool",
        {
            "name": "multiplier",
            "description": "Multiplies number by 10",
            "code": code,
            "parameters": {"x": {"type": "int"}},
        },
        obs,
        cfg,
    )
    assert res_synth.ok is True
    assert "multiplier" in res_synth.message

    # 2. List action
    res_list = registry.execute("list_synthesized_tools", {}, obs, cfg)
    assert res_list.ok is True
    assert "multiplier" in res_list.message

    # 3. Execute action
    res_exec = registry.execute(
        "execute_synthesized_tool",
        {"name": "multiplier", "args": {"x": 7}},
        obs,
        cfg,
    )
    assert res_exec.ok is True
    assert "70" in res_exec.message


def test_delete_synthesized_tool(tmp_path):
    mgr = SynthesizedToolManager(tools_dir=tmp_path / "tools")
    mgr.synthesize(
        name="temp_tool",
        description="Temporary tool for testing delete",
        code="def run(): return 'done'",
    )
    assert (tmp_path / "tools" / "temp_tool.py").exists()

    del_ok, del_msg = mgr.delete_tool("temp_tool")
    assert del_ok is True
    assert not (tmp_path / "tools" / "temp_tool.py").exists()
