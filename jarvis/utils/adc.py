"""Where Google's Application Default Credentials file lives, on any platform.

``gcloud auth application-default login`` writes that file to a different place
on each platform, and two unrelated parts of Jarvis need to find it:

* the agent's Vertex brain (:mod:`jarvis.agent.brain`) reads it directly so that
  obtaining a token costs no ``google-auth`` import and no ``gcloud`` subshell;
* the live-voice readiness report (:mod:`jarvis.live.readiness`) has to say
  whether Vertex can authenticate at all before the user presses the button.

Those two searched separately, and the brain's copy was hard-coded to Windows'
``%APPDATA%``: on Linux and macOS the fast path could never fire while the
report happily announced credentials the brain could not read. One search, one
answer - this module is stdlib-only and imports nothing from the package, so
both callers can depend on it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Mapping

#: The credential file's location relative to a per-user root. The Windows
#: location is checked because that is where Jarvis usually runs; a Linux/macOS
#: checkout uses the first entry.
ADC_RELATIVE_PATHS = (
    Path(".config") / "gcloud" / "application_default_credentials.json",
    Path("gcloud") / "application_default_credentials.json",
)


def search_paths(env: Mapping[str, str] | None = None) -> list[Path]:
    """Every place the ADC file may live, in the order worth trying.

    ``GOOGLE_APPLICATION_CREDENTIALS`` wins when it is set, because that is the
    variable every Google client library honours.
    """
    env = os.environ if env is None else env
    found: list[Path] = []
    explicit = str(env.get("GOOGLE_APPLICATION_CREDENTIALS", "") or "").strip()
    if explicit:
        found.append(Path(explicit))
    home = Path.home()
    appdata = str(env.get("APPDATA", "") or "").strip()
    for relative in ADC_RELATIVE_PATHS:
        found.append(home / relative)
        if appdata:
            found.append(Path(appdata) / relative)
    return found


def adc_path(
    env: Mapping[str, str] | None = None,
    search: Iterable[Path] | None = None,
) -> Path | None:
    """The Application Default Credentials file that exists, if any.

    ``search`` overrides the platform search, which is what lets the readiness
    report be tested without touching the real machine's credentials.
    """
    for candidate in (list(search) if search is not None else search_paths(env)):
        try:
            if Path(candidate).is_file():
                return Path(candidate)
        except OSError:
            continue
    return None
