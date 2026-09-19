"""Shared test isolation.

Live voice *measures* which models hear audio and writes the verdict to
``~/.jarvis/live_audio_models.json`` (see ``jarvis.live.gemini_live``), because
that verdict is a property of the API key rather than of a process. Tests must
not read it - a model demoted on the machine running the suite would silently
reorder another test's expectations - and must not write it either.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_live_audio_state(tmp_path_factory):
    """Point the measured live-voice verdicts at a throwaway file for the run."""
    state = tmp_path_factory.mktemp("live-state") / "live_audio_models.json"
    previous = os.environ.get("JARVIS_LIVE_AUDIO_STATE")
    os.environ["JARVIS_LIVE_AUDIO_STATE"] = str(state)
    yield state
    if previous is None:
        os.environ.pop("JARVIS_LIVE_AUDIO_STATE", None)
    else:
        os.environ["JARVIS_LIVE_AUDIO_STATE"] = previous
