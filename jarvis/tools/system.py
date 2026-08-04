"""System-level tools: shell commands, clipboard, web, and machine info.

Shell execution is guarded by a denylist from :class:`SafetyConfig`; anything
matching a blocked pattern is refused rather than run.
"""

from __future__ import annotations

import os
import subprocess
import webbrowser
from html.parser import HTMLParser


# Process images whose death would take Jarvis (or its host shell) with it.
_SELF_IMAGES = ("python", "powershell", "pwsh", "cmd.exe", "conhost",
                "windowsterminal", "wt.exe", "openconsole")


def _threatens_self(command: str) -> bool:
    """True when a command could kill Jarvis's own process or host terminal:
    taskkill/Stop-Process/kill aimed at our image names, our PID, or any
    ancestor PID (the shell/terminal that launched us)."""
    low = command.lower()
    if not any(k in low for k in ("taskkill", "stop-process", "kill ")):
        return False
    if any(img in low for img in _SELF_IMAGES):
        return True
    import os
    import re

    pids = {str(os.getpid())}
    try:
        import psutil  # type: ignore

        pids.update(str(p.pid) for p in psutil.Process().parents()[:3])
    except Exception:
        pass
    return bool(set(re.findall(r"\b(\d{2,7})\b", low)) & pids)


def run_command(command: str, blocked: tuple[str, ...] = (),
                timeout: int = 60, cwd: str | None = None) -> str:
    low = command.lower()
    for pat in blocked:
        if pat.lower() in low:
            return f"refused: command matches blocked pattern '{pat.strip()}'"
    if _threatens_self(command):
        return ("refused: that command could kill Jarvis's own terminal or "
                "process. If you really want those processes stopped, ask "
                "the user to do it manually (e.g. via Task Manager).")
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd or None, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"command timed out after {timeout}s"
    except Exception as exc:
        return f"failed to run: {exc}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parts = [f"exit code {proc.returncode}"]
    if out:
        parts.append("stdout:\n" + out[:6000])
    if err:
        parts.append("stderr:\n" + err[:2000])
    return "\n".join(parts)


def run_python(code: str, timeout: int = 60, cwd: str | None = None) -> str:
    """Run a Python snippet in a fresh subprocess and return its output.

    A general-purpose compute action: use any installed library, crunch data,
    do maths, parse text, generate content - without the shell-quoting pain of
    run_command "python -c ...". It runs isolated in its OWN process, so a
    crash or an infinite loop cannot take Jarvis down (it is killed at the
    timeout). Only what the code print()s comes back.
    """
    code = code or ""
    if not code.strip():
        return "python needs code to run"
    try:
        timeout = max(1, min(600, int(timeout or 60)))
    except (TypeError, ValueError):
        timeout = 60
    import sys
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            timeout=timeout, cwd=cwd or None, stdin=subprocess.DEVNULL,
        )

    except subprocess.TimeoutExpired:
        return f"python timed out after {timeout}s (raise the timeout if it needs longer)"
    except Exception as exc:
        return f"failed to run python: {exc}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parts = [f"exit code {proc.returncode}"]
    if out:
        parts.append("stdout:\n" + out[:6000])
    if err:
        parts.append("stderr:\n" + err[:2000])
    if not out and not err:
        parts.append("(no output - remember to print() any result you want to see)")
    return "\n".join(parts)


def http_request(method: str, url: str, headers=None, params=None,
                 json_body=None, data=None, timeout: int = 30,
                 max_chars: int = 6000) -> str:
    """Call any web API and return the status line plus the raw response body.

    Unlike read_url (which GETs a page and strips it to article text), this
    speaks to JSON/REST APIs: pick the method, send headers (e.g. an auth
    token), query params and a JSON or form body. Use it for webhooks, cloud
    services, smart-home endpoints - anything with an API.
    """
    import requests  # type: ignore

    url = (url or "").strip()
    if not url:
        return "http_request needs a url"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    method = (method or "GET").strip().upper()
    try:
        timeout = max(1, min(120, int(timeout or 30)))
    except (TypeError, ValueError):
        timeout = 30
    kw: dict = {"headers": headers or None, "params": params or None,
                "timeout": timeout}
    if json_body is not None:
        kw["json"] = json_body
    elif data is not None:
        kw["data"] = data
    try:
        r = requests.request(method, url, **kw)
    except Exception as exc:
        return f"{method} {url} failed: {exc}"
    body = r.text or ""
    max_chars = max(200, min(30000, int(max_chars or 6000)))
    if len(body) > max_chars:
        body = body[:max_chars] + f"\n...(truncated; {len(body)} chars total)"
    ctype = (r.headers.get("content-type") or "").split(";")[0]
    head = f"{method} {url} -> HTTP {r.status_code}" + (f" ({ctype})" if ctype else "")
    return f"{head}\n{body}" if body.strip() else f"{head} (empty body)"


def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"opened {url}"


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class _TextExtractor(HTMLParser):
    """Collect the visible text of an HTML page, skipping non-content tags."""
    _SKIP = {"script", "style", "noscript", "template", "head", "svg"}
    _BREAK = {"p", "br", "div", "li", "tr", "table", "section", "article",
              "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass                               # keep whatever was extracted
    lines = []
    for line in "".join(parser.parts).splitlines():
        line = " ".join(line.split())      # collapse runs of whitespace
        if line:
            lines.append(line)
    return "\n".join(lines)


def read_url(url: str, max_chars: int = 8000) -> str:
    """Fetch a web page and return its readable text - no browser involved."""
    import requests  # type: ignore

    url = (url or "").strip()
    if not url:
        return "read_url needs a url"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    max_chars = max(500, min(30000, int(max_chars or 8000)))
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=20)
    except Exception as exc:
        return f"could not fetch {url}: {exc}"
    if r.status_code >= 400:
        return f"could not fetch {url}: HTTP {r.status_code}"
    ctype = (r.headers.get("content-type") or "").lower()
    if "html" in ctype or ctype == "":
        text = _html_to_text(r.text)
    elif "text" in ctype or "json" in ctype or "xml" in ctype:
        text = r.text.strip()
    else:
        return f"{url} is not a readable text page (content-type: {ctype})"
    if not text:
        return f"{url} returned no readable text"
    if len(text) > max_chars:
        text = (text[:max_chars]
                + f"\n... (truncated; {len(text)} chars total)")
    return f"content of {url}:\n{text}"


def web_search(query: str, max_results: int = 5) -> str:
    """DuckDuckGo search, no API key. Returns a compact text block Jarvis can
    read out or act on: an instant answer (when DDG has one) plus the top
    result titles, links and snippets.

    Real web results come from the ``ddgs`` client, which handles DuckDuckGo's
    anti-bot handshake (naive requests-scraping gets a 202 challenge page and
    zero results). The JSON Instant-Answer API is layered on top for quick
    factual answers.
    """
    query = (query or "").strip()
    if not query:
        return "web_search needs a query"
    max_results = max(1, min(10, int(max_results or 5)))
    lines: list[str] = []

    answer = _instant_answer(query)
    if answer:
        lines.append(answer)

    results, lib_ok = _ddg_text(query, max_results)
    for title, url, snippet in results:
        block = f"- {title}\n  {url}"
        if snippet:
            block += f"\n  {snippet}"
        lines.append(block)

    if not lines:
        if not lib_ok:
            return ("web search needs the DuckDuckGo client - "
                    "install it with:  pip install ddgs")
        return f"no results for '{query}' (try rephrasing, or open_url a search)"
    return "\n".join(lines)


def _instant_answer(query: str) -> str:
    """DuckDuckGo Instant Answer API - a one-line factual answer when it has
    one (empty for most general queries; that's what _ddg_text covers)."""
    import requests  # type: ignore

    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers={"User-Agent": _UA}, timeout=10)
        data = r.json()
    except Exception:
        return ""
    text = (data.get("AbstractText") or data.get("Answer") or "").strip()
    if not text:
        return ""
    src = (data.get("AbstractSource") or "").strip()
    return f"Answer: {text}" + (f"  ({src})" if src else "")


def _ddg_text(query: str, max_results: int) -> tuple[list[tuple[str, str, str]], bool]:
    """Return (results, library_available). Each result is (title, url, snippet)."""
    try:
        from ddgs import DDGS  # type: ignore
    except Exception:
        try:
            from duckduckgo_search import DDGS  # type: ignore  # older name
        except Exception:
            return [], False
    try:
        with DDGS() as ddgs:
            rows = list(ddgs.text(query, max_results=max_results))
    except Exception:
        return [], True
    out: list[tuple[str, str, str]] = []
    for r in rows:
        title = (r.get("title") or "").strip()
        url = (r.get("href") or r.get("url") or "").strip()
        body = (r.get("body") or "").strip()
        if title and url:
            out.append((title, url, body[:200]))
    return out, True


def media_control(op: str, value: int | None = None) -> str:
    """Audio/media control via the keyboard's virtual media keys - no deps.

    One volume key press = 2 units of 100, so volume moves in ~2% steps.
    set_volume first drops to a known zero, then raises - deterministic
    percent without needing an audio API.
    """
    import pyautogui as pg  # type: ignore

    op = (op or "").strip().lower().replace("-", "_").replace(" ", "_")
    simple = {"play_pause": "playpause", "play": "playpause",
              "pause": "playpause",
              "next": "nexttrack", "next_track": "nexttrack",
              "prev": "prevtrack", "previous": "prevtrack",
              "prev_track": "prevtrack",
              "mute": "volumemute", "unmute": "volumemute"}
    if op in simple:
        pg.press(simple[op])
        return f"media: {op}"
    if op in {"volume_up", "volume_down"}:
        pct = max(2, min(100, int(value if value is not None else 10)))
        pg.press("volumeup" if op == "volume_up" else "volumedown",
                 presses=round(pct / 2), interval=0.0)
        return f"volume {op.split('_')[1]} ~{pct}%"
    if op in {"set_volume", "set"}:
        if value is None:
            return "set_volume needs 'value' (0-100)"
        pct = max(0, min(100, int(value)))
        pg.press("volumedown", presses=50, interval=0.0)   # known zero point
        if pct:
            pg.press("volumeup", presses=round(pct / 2), interval=0.0)
        return f"volume set to ~{pct}%"
    return (f"unknown media op '{op}' - use play_pause, next, prev, mute, "
            "volume_up, volume_down, or set_volume")


# Registered AppId so the toast reliably shows on stock Windows 10/11.
_TOAST_APPID = ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell"
                "\\v1.0\\powershell.exe")

def notify(message: str, title: str = "JARVIS") -> str:
    """Show a Windows toast notification - stock PowerShell, no deps.

    Text travels via environment variables, never string-built into the
    script, so quotes or anything else in the message cannot break out.
    """
    message = (message or "").strip()
    if not message:
        return "notify needs a message"
    ps = (
        "$null = [Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime];"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::"
        "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$texts = $t.GetElementsByTagName('text');"
        "$null = $texts.Item(0).AppendChild($t.CreateTextNode($env:JARVIS_N_TITLE));"
        "$null = $texts.Item(1).AppendChild($t.CreateTextNode($env:JARVIS_N_MSG));"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "$env:JARVIS_N_APPID).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    env = dict(os.environ, JARVIS_N_TITLE=(title or "JARVIS")[:60],
               JARVIS_N_MSG=message[:200], JARVIS_N_APPID=_TOAST_APPID)
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            env=env, capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return f"could not show notification: {exc}"
    if proc.returncode != 0:
        return f"notification failed: {(proc.stderr or '').strip()[:200]}"
    return f"notification shown: {message[:60]}"


def clipboard_read() -> str:
    try:
        import pyperclip  # type: ignore

        return pyperclip.paste()
    except Exception:
        try:
            import pyautogui  # type: ignore

            return pyautogui.paste()  # type: ignore[attr-defined]
        except Exception as exc:
            return f"clipboard unavailable: {exc}"


def clipboard_write(text: str) -> str:
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return "copied to clipboard"
    except Exception as exc:
        return f"clipboard unavailable: {exc}"


def system_status() -> str:
    """A JARVIS-style diagnostics readout: CPU, memory, disk, battery, uptime.

    Everything past the OS line is best-effort - psutil may be missing a given
    sensor (e.g. no battery on a desktop), so each block degrades on its own.
    """
    import platform

    lines = [f"os: {platform.platform()}"]
    try:
        import psutil  # type: ignore
    except Exception:
        lines.append("(install psutil for CPU/memory/disk/battery detail)")
        return "\n".join(lines)

    def _gb(n: int) -> str:
        return f"{n / 2**30:.1f} GB"

    try:
        lines.append(f"cpu: {psutil.cpu_percent(interval=0.2):.0f}% "
                     f"over {psutil.cpu_count(logical=True)} threads")
    except Exception:
        pass
    try:
        vm = psutil.virtual_memory()
        lines.append(f"memory: {_gb(vm.used)} / {_gb(vm.total)} used ({vm.percent:.0f}%)")
    except Exception:
        pass
    try:
        du = psutil.disk_usage("/")
        lines.append(f"disk: {_gb(du.used)} / {_gb(du.total)} used ({du.percent:.0f}%)")
    except Exception:
        pass
    try:
        bat = psutil.sensors_battery()
        if bat is not None:
            plug = "charging" if bat.power_plugged else "on battery"
            lines.append(f"battery: {bat.percent:.0f}% ({plug})")
    except Exception:
        pass
    try:
        import time
        up = int(time.time() - psutil.boot_time())
        h, m = up // 3600, (up % 3600) // 60
        lines.append(f"uptime: {h}h {m}m")
    except Exception:
        pass
    return "\n".join(lines)
