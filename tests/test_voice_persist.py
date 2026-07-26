"""_persist_voice rewrites the one voice_enabled line and keeps comments."""

import jarvis.console as console


def test_persist_voice_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("# keep me\nvoice_enabled: false\n", encoding="utf-8")
    monkeypatch.setattr("jarvis.config.CONFIG_PATH", p)
    console._persist_voice(True)
    assert "voice_enabled: true" in p.read_text(encoding="utf-8")
    assert "# keep me" in p.read_text(encoding="utf-8")
    console._persist_voice(False)
    assert "voice_enabled: false" in p.read_text(encoding="utf-8")


def test_persist_voice_appends_when_missing(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("brain:\n  backend: gemini\n", encoding="utf-8")
    monkeypatch.setattr("jarvis.config.CONFIG_PATH", p)
    console._persist_voice(True)
    assert p.read_text(encoding="utf-8").endswith("voice_enabled: true\n")
