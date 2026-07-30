"""Direct account connectors: Gmail, WhatsApp, Discord.

These sit *below* MCP on purpose. An MCP server is a subprocess to launch and a
protocol to speak; these talk straight to the provider (IMAP for Gmail, REST for
the other two), so "what's unread?" costs one round trip instead of opening a
browser, screenshotting it and clicking through a dozen turns. Three things keep
it fast:

  * the Gmail IMAP connection is reused - the TLS handshake plus LOGIN is most
    of the latency, and paying it once per session instead of once per question
    is the single biggest win here;
  * every read is cached for ``JARVIS_CONNECTOR_TTL`` seconds (default 60), so
    a repeated question answers instantly with no network at all;
  * results come back as plain text with ``needs_observe=False``, so the agent
    loop skips its perception pass entirely.

Credentials come from the environment; ``.env`` at the project root is loaded by
:mod:`jarvis.config` at startup. Nothing here is configured by default - an
unconfigured service says exactly which variables it wants and where to get
them, and :func:`note` keeps it out of the model's prompt until it works.

    GMAIL_ADDRESS, GMAIL_APP_PASSWORD    - myaccount.google.com/apppasswords
    DISCORD_BOT_TOKEN                    - discord.com/developers/applications
    WHATSAPP_TOKEN, WHATSAPP_PHONE_ID    - developers.facebook.com (Cloud API)
    WHATSAPP_INBOX                       - optional JSONL your webhook appends to
"""

from __future__ import annotations

import email
import email.header
import json
import os
import re
import threading
import time
from pathlib import Path

_CAP = 4000                   # chars of any result handed back to the model
_MAX_CACHE = 64               # cached read results before the oldest are dropped

_cache: dict[tuple, tuple[float, str]] = {}
_cache_lock = threading.Lock()


class ConnectorError(RuntimeError):
    """A connector could not answer - message is model-facing and actionable."""


def _ttl() -> float:
    try:
        return max(0.0, float(os.getenv("JARVIS_CONNECTOR_TTL", "60") or 60))
    except ValueError:
        return 60.0


def _cached(key: tuple, produce) -> str:
    """Memoise a read for :func:`_ttl` seconds. ``produce`` runs outside the
    lock - a slow network call must never block a cache hit on another key."""
    ttl = _ttl()
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    text = produce()
    with _cache_lock:
        _cache[key] = (time.time(), text)
        if len(_cache) > _MAX_CACHE:
            for old, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:_MAX_CACHE // 2]:
                _cache.pop(old, None)
    return text


def _env(*names: str) -> tuple[str, ...]:
    return tuple(os.getenv(n, "").strip() for n in names)


# --------------------------------------------------------------------------- #
# Gmail (IMAP - stdlib, no dependency, and faster than the REST API for reads)
# --------------------------------------------------------------------------- #
_GMAIL_HINT = (
    "Gmail is not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in the "
    "project's .env file. The app password is the 16-character one from "
    "myaccount.google.com/apppasswords (2-step verification must be on) - a "
    "normal account password will NOT work over IMAP.")

_imap = None                              # the one reused connection
_imap_lock = threading.RLock()            # imaplib objects are not thread-safe


def _gmail_login():
    global _imap
    user, password = _env("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")
    password = password.replace(" ", "")   # Google shows it in groups of four
    if not user or not password:
        raise ConnectorError(_GMAIL_HINT)
    if _imap is not None:
        try:
            _imap.noop()
            return _imap
        except Exception:
            _imap = None                   # dropped by the server: reconnect
    import imaplib
    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        conn.login(user, password)
    except imaplib.IMAP4.error as exc:
        raise ConnectorError(f"Gmail rejected the login ({exc}). {_GMAIL_HINT}")
    _imap = conn
    return conn


def _gmail(op: str, query: str = "", target: str = "", limit: int = 10) -> str:
    with _imap_lock:
        try:
            return _gmail_do(op, query, target, limit)
        except ConnectorError:
            raise
        except Exception:
            global _imap
            _imap = None                   # half-dead connection: one clean retry
            try:
                return _gmail_do(op, query, target, limit)
            except ConnectorError:
                raise
            except Exception as exc:
                raise ConnectorError(f"gmail {op} failed: {exc}")


def _gmail_do(op: str, query: str, target: str, limit: int) -> str:
    conn = _gmail_login()
    # readonly: answering "what's in my inbox" must never mark mail as read.
    conn.select("INBOX", readonly=True)
    if op == "read":
        if not target.strip():
            raise ConnectorError("gmail read needs 'target' - the [id] shown "
                                 "beside a message in a previous listing")
        return _gmail_body(conn, target.strip())
    if op == "search":
        if not query.strip():
            raise ConnectorError("gmail search needs a 'query' (Gmail search "
                                 "syntax, e.g. 'from:bank is:unread newer_than:7d')")
        # X-GM-RAW hands the query to Gmail's own search engine, so the model
        # can use the syntax it already knows.
        # ponytail: json.dumps quotes/escapes it; non-ASCII terms would need an
        # IMAP literal with CHARSET UTF-8 - add that if a search ever needs it.
        criteria = ("X-GM-RAW", json.dumps(query, ensure_ascii=True))
    elif op == "unread":
        criteria = ("UNSEEN",)
    else:
        criteria = ("ALL",)
    typ, data = conn.uid("SEARCH", None, *criteria)
    if typ != "OK":
        raise ConnectorError(f"gmail search failed: {data}")
    uids = (data[0] or b"").split()
    if not uids:
        return "no unread mail" if op == "unread" else "nothing matched"
    wanted = uids[-limit:]                 # SEARCH returns oldest-first
    typ, resp = conn.uid("FETCH", b",".join(wanted).decode(),
                         "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    if typ != "OK":
        raise ConnectorError(f"gmail fetch failed: {resp}")
    rows: list[tuple[int, str]] = []
    for part in resp:
        if not isinstance(part, tuple) or len(part) < 2:
            continue
        found = re.search(rb"UID (\d+)", part[0] or b"")
        uid = int(found.group(1)) if found else 0
        msg = email.message_from_bytes(part[1])
        rows.append((uid, f"[{uid}] {_hdr(msg, 'From')}\n      "
                          f"{_hdr(msg, 'Subject') or '(no subject)'}"
                          f"  · {_hdr(msg, 'Date')}"))
    rows.sort(key=lambda r: r[0], reverse=True)      # newest first
    head = (f"{len(uids)} unread" if op == "unread"
            else f"{len(uids)} match(es)") + f", showing {len(rows)}:"
    return head + "\n" + "\n".join(r[1] for r in rows)


def _gmail_body(conn, uid: str) -> str:
    typ, resp = conn.uid("FETCH", uid, "(BODY.PEEK[])")
    raw = next((p[1] for p in resp if isinstance(p, tuple) and len(p) > 1), None)
    if typ != "OK" or raw is None:
        raise ConnectorError(f"no message with id {uid} (ids come from a "
                             "gmail unread/search listing and are per-account)")
    msg = email.message_from_bytes(raw)
    body, html = "", ""
    for part in msg.walk():
        if "attachment" in str(part.get("Content-Disposition", "")):
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            text = part.get_payload(decode=True).decode(
                part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if ctype == "text/plain" and not body:
            body = text
        elif ctype == "text/html" and not html:
            html = text
    if not body and html:
        from .system import _html_to_text
        body = _html_to_text(html)
    return (f"From: {_hdr(msg, 'From')}\nDate: {_hdr(msg, 'Date')}\n"
            f"Subject: {_hdr(msg, 'Subject')}\n\n{body.strip() or '(empty body)'}")


def _hdr(msg, name: str) -> str:
    """One decoded header line (subjects are often RFC 2047 encoded)."""
    raw = msg.get(name, "")
    try:
        parts = email.header.decode_header(raw)
    except Exception:
        return str(raw).strip()
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", "replace"))
        else:
            out.append(chunk)
    return " ".join("".join(out).split())


# --------------------------------------------------------------------------- #
# Discord (bot REST API)
# --------------------------------------------------------------------------- #
_DISCORD_API = "https://discord.com/api/v10"
_DISCORD_HINT = (
    "Discord is not configured. Create an application at "
    "discord.com/developers/applications, add a Bot, copy its token into "
    "DISCORD_BOT_TOKEN in .env, turn ON the 'Message Content Intent' under "
    "Bot > Privileged Gateway Intents (without it message text comes back "
    "empty), then invite the bot to your server with the View Channels and "
    "Read Message History permissions.")


def _discord_get(path: str, params: dict | None = None):
    import requests

    (token,) = _env("DISCORD_BOT_TOKEN")
    if not token:
        raise ConnectorError(_DISCORD_HINT)
    try:
        r = requests.get(_DISCORD_API + path, params=params, timeout=20,
                         headers={"Authorization": f"Bot {token}",
                                  "User-Agent": "Jarvis/1.0"})
    except Exception as exc:
        raise ConnectorError(f"could not reach Discord: {exc}")
    if r.status_code == 401:
        raise ConnectorError("Discord rejected the token (401). " + _DISCORD_HINT)
    if r.status_code == 403:
        raise ConnectorError("Discord refused access (403) - the bot is in the "
                             "server but lacks View Channel / Read Message "
                             "History on that channel.")
    if r.status_code == 429:
        raise ConnectorError("Discord rate-limited the request (429) - retry in "
                             f"{r.json().get('retry_after', '?')}s.")
    if r.status_code >= 400:
        raise ConnectorError(f"Discord {path} -> HTTP {r.status_code}: "
                             f"{r.text[:200]}")
    return r.json()


def _discord_guilds() -> list[dict]:
    return _discord_get("/users/@me/guilds")


def _discord_channels(guild_id: str) -> list[dict]:
    chans = _discord_get(f"/guilds/{guild_id}/channels")
    return [c for c in chans if c.get("type") in (0, 5)]      # text + announcement


def _resolve_guild(target: str) -> dict:
    guilds = _discord_guilds()
    if not guilds:
        raise ConnectorError("the bot is not in any server yet - invite it from "
                             "the Developer Portal's OAuth2 URL generator.")
    if not target:
        if len(guilds) == 1:
            return guilds[0]
        names = ", ".join(g["name"] for g in guilds[:20])
        raise ConnectorError(f"which server? the bot is in: {names}")
    low = target.strip().lower()
    for g in guilds:
        if g["id"] == target.strip() or g["name"].lower() == low:
            return g
    for g in guilds:
        if low in g["name"].lower():
            return g
    names = ", ".join(g["name"] for g in guilds[:20])
    raise ConnectorError(f"no server matching '{target}'. The bot is in: {names}")


def _resolve_channel(target: str) -> dict:
    """A channel id, or a #name looked up across every server the bot is in.
    Name lookup costs extra calls, so it is worth taking the id from a previous
    'channels' listing when reading the same channel repeatedly."""
    target = target.strip().lstrip("#")
    if target.isdigit():
        return {"id": target, "name": target}
    if not target:
        raise ConnectorError("discord messages needs 'target' - a channel id or "
                             "#name (list them with op 'channels')")
    low = target.lower()
    partial = None
    for guild in _discord_guilds():
        for chan in _discord_channels(guild["id"]):
            name = chan.get("name", "")
            if name.lower() == low:
                return chan
            if partial is None and low in name.lower():
                partial = chan
    if partial is not None:
        return partial
    raise ConnectorError(f"no text channel named '{target}' in any server the "
                         "bot can see")


def _discord(op: str, query: str = "", target: str = "", limit: int = 10) -> str:
    if op in ("guilds", "servers"):
        guilds = _discord_guilds()
        if not guilds:
            raise ConnectorError("the bot is not in any server yet.")
        return "servers:\n" + "\n".join(
            f"  [{g['id']}] {g['name']}" for g in guilds)

    if op == "channels":
        guild = _resolve_guild(target or query)
        chans = _discord_channels(guild["id"])
        if not chans:
            return f"{guild['name']}: no text channels the bot can see"
        return f"text channels in {guild['name']}:\n" + "\n".join(
            f"  [{c['id']}] #{c.get('name', '?')}" for c in chans)

    if op in ("messages", "unread", "recent"):
        chan = _resolve_channel(target or query)
        msgs = _discord_get(f"/channels/{chan['id']}/messages",
                            {"limit": max(1, min(100, limit))})
        if not msgs:
            return f"#{chan.get('name', chan['id'])} has no messages"
        lines = []
        for m in reversed(msgs):                       # oldest -> newest reads better
            author = (m.get("author") or {}).get("username", "?")
            when = str(m.get("timestamp", ""))[:16].replace("T", " ")
            text = " ".join(str(m.get("content", "")).split())
            if not text and m.get("attachments"):
                text = f"({len(m['attachments'])} attachment(s))"
            if not text and m.get("embeds"):
                text = "(embed)"
            if not text:
                text = "(no readable content - enable Message Content Intent)"
            lines.append(f"  {when} {author}: {text}")
        return f"#{chan.get('name', chan['id'])} last {len(msgs)}:\n" + "\n".join(lines)

    raise ConnectorError(f"unknown discord op '{op}' - use guilds, channels or "
                         "messages")


# --------------------------------------------------------------------------- #
# WhatsApp (Meta Cloud API)
# --------------------------------------------------------------------------- #
_WHATSAPP_API = "https://graph.facebook.com/v21.0"
_WHATSAPP_HINT = (
    "WhatsApp is not configured. Set WHATSAPP_TOKEN and WHATSAPP_PHONE_ID in "
    ".env from your Meta app at developers.facebook.com (WhatsApp > API Setup).")
_WHATSAPP_READ_HINT = (
    "WhatsApp's Cloud API has no endpoint for reading past messages - Meta "
    "pushes incoming messages to a webhook instead, so there is nothing to poll. "
    "Two ways to give me a fast inbox: (1) point WHATSAPP_INBOX in .env at a "
    "JSONL file your webhook appends each message to, and I will read it "
    "instantly; or (2) run a WhatsApp MCP bridge and add it with the mcp "
    "action, which exposes full chat history as MCP tools.")


def _whatsapp(op: str, query: str = "", target: str = "", limit: int = 10) -> str:
    if op in ("messages", "unread", "recent", "chats"):
        return _whatsapp_inbox(query or target, limit)

    if op == "profile":
        token, phone_id = _env("WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID")
        if not token or not phone_id:
            raise ConnectorError(_WHATSAPP_HINT)
        import requests
        try:
            r = requests.get(
                f"{_WHATSAPP_API}/{phone_id}/whatsapp_business_profile",
                params={"fields": "about,address,description,email,vertical,"
                                  "websites"},
                headers={"Authorization": f"Bearer {token}"}, timeout=20)
        except Exception as exc:
            raise ConnectorError(f"could not reach the WhatsApp API: {exc}")
        if r.status_code == 401:
            raise ConnectorError("WhatsApp rejected the token (401) - Cloud API "
                                 "tokens from the test flow expire after 24h; "
                                 "generate a permanent System User token.")
        if r.status_code >= 400:
            raise ConnectorError(f"WhatsApp profile -> HTTP {r.status_code}: "
                                 f"{r.text[:200]}")
        return "whatsapp business profile:\n" + json.dumps(r.json(), indent=2)[:_CAP]

    raise ConnectorError(f"unknown whatsapp op '{op}' - use messages or profile")


def _whatsapp_inbox(contains: str, limit: int) -> str:
    """Read the webhook-fed JSONL inbox. Each line is one message object; we
    accept either a flat {from,text,timestamp} record or a raw Cloud API webhook
    payload, since which one you get depends on how the webhook was written."""
    (path,) = _env("WHATSAPP_INBOX")
    if not path:
        raise ConnectorError(_WHATSAPP_READ_HINT)
    p = Path(os.path.expanduser(path))
    if not p.exists():
        raise ConnectorError(f"WHATSAPP_INBOX points at {p}, which does not "
                             "exist yet - no message has been received, or the "
                             "webhook is not writing there.")
    rows: list[str] = []
    low = (contains or "").strip().lower()
    # ponytail: whole-file read. These inboxes are append-only chat logs, not
    # server logs; switch to a tail-seek if one ever grows past a few MB.
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        for msg in _whatsapp_records(rec):
            sender = str(msg.get("from", "?"))
            body = str((msg.get("text") or {}).get("body", "")
                       if isinstance(msg.get("text"), dict) else msg.get("text", ""))
            when = str(msg.get("timestamp", ""))
            if when.isdigit():
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(when)))
            row = f"  {when} {sender}: {' '.join(body.split())}"
            if not low or low in row.lower():
                rows.append(row)
    if not rows:
        return ("no WhatsApp messages" + (f" matching '{contains}'" if low else "")
                + f" in {p.name}")
    rows = rows[-max(1, limit):]
    return f"whatsapp inbox ({len(rows)} shown):\n" + "\n".join(rows)


def _whatsapp_records(rec) -> list[dict]:
    """Pull message objects out of either shape of inbox line."""
    if not isinstance(rec, dict):
        return []
    if "from" in rec or "text" in rec:
        return [rec]
    out: list[dict] = []
    for entry in rec.get("entry", []) or []:
        for change in (entry or {}).get("changes", []) or []:
            value = (change or {}).get("value", {}) or {}
            out.extend(m for m in value.get("messages", []) or []
                       if isinstance(m, dict))
    return out


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
_SERVICES = {
    "gmail": {
        "fn": _gmail,
        "env": ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"),
        "ops": "unread, search (query=Gmail search syntax), read (target=id)",
    },
    "discord": {
        "fn": _discord,
        "env": ("DISCORD_BOT_TOKEN",),
        "ops": "guilds, channels (target=server), messages (target=channel id or #name)",
    },
    "whatsapp": {
        "fn": _whatsapp,
        "env": ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID"),
        "ops": "messages, profile",
    },
}

_ALIASES = {"mail": "gmail", "email": "gmail", "google": "gmail",
            "gmai": "gmail", "wa": "whatsapp", "whats app": "whatsapp",
            "discord.com": "discord"}


def configured(service: str) -> bool:
    spec = _SERVICES.get(service)
    if spec is None:
        return False
    if service == "whatsapp" and os.getenv("WHATSAPP_INBOX", "").strip():
        return True          # inbox-only setups can read without API creds
    return all(os.getenv(name, "").strip() for name in spec["env"])


def fetch(service: str, op: str, query: str = "", target: str = "",
          limit: int = 10) -> str:
    """Run one connector read. Raises :class:`ConnectorError` with an
    actionable message; every result is capped and briefly cached."""
    service = _ALIASES.get(str(service or "").strip().lower(),
                           str(service or "").strip().lower())
    spec = _SERVICES.get(service)
    if spec is None:
        raise ConnectorError(f"unknown connector '{service}' - available: "
                             + ", ".join(_SERVICES))
    op = str(op or "").strip().lower() or "unread"
    try:
        limit = max(1, min(50, int(limit or 10)))
    except (TypeError, ValueError):
        limit = 10
    key = (service, op, str(query), str(target), limit)
    text = _cached(key, lambda: spec["fn"](op, str(query or ""),
                                           str(target or ""), limit))
    return text[:_CAP] + ("\n...(truncated)" if len(text) > _CAP else "")


def invalidate(service: str = "") -> None:
    """Drop cached reads so the next call hits the network (used by /connect
    test, and worth calling after anything that changes the account state)."""
    with _cache_lock:
        if not service:
            _cache.clear()
        else:
            for key in [k for k in _cache if k[0] == service]:
                _cache.pop(key, None)


def status() -> str:
    """Human-readable configuration report for the ':connect' console command."""
    rows = []
    for name, spec in _SERVICES.items():
        if configured(name):
            rows.append(f"  {name:<9} ready      (ops: {spec['ops']})")
        else:
            missing = [e for e in spec["env"] if not os.getenv(e, "").strip()]
            rows.append(f"  {name:<9} not set up (needs {', '.join(missing)} "
                        "in .env)")
    return "connectors:\n" + "\n".join(rows)


def note() -> str:
    """The CONNECTORS section injected into the agent's system prompt. Empty
    when nothing is configured, so the prompt never carries dead weight."""
    ready = [n for n in _SERVICES if configured(n)]
    if not ready:
        return ""
    lines = [f"  {n} -> {_SERVICES[n]['ops']}" for n in ready]
    return ("\n\n=== CONNECTORS (call with the 'connector' action - instant, no "
            "browser) ===\n" + "\n".join(lines) +
            "\n  Use these instead of opening Gmail/Discord/WhatsApp in a "
            "browser: one call answers in under a second.\n"
            "=================================================================")


def warm(background: bool = True) -> None:
    """Pre-open Gmail's IMAP connection and pre-fetch the unread list so the
    first question of the session is already answered from cache. Failures are
    swallowed - a connector that is down must never delay startup."""
    if not configured("gmail"):
        return

    def _work():
        try:
            fetch("gmail", "unread")
        except Exception:
            pass

    if background:
        threading.Thread(target=_work, daemon=True, name="connector-warm").start()
    else:
        _work()
