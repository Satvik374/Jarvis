"""OpenAI Codex CLI "Sign in with ChatGPT" OAuth flow.

Implements Authorization Code + PKCE (S256) flow:
- client_id: app_EMoamEEZ73f0CkXaXp7hrann
- issuer: https://auth.openai.com
- redirect_uri: http://localhost:1455/auth/callback
- scope: openid profile email offline_access
- extra authorize params: prompt=login, id_token_add_organizations=true, codex_cli_simplified_flow=true
- local HTTP listener on port 1455
- token exchange & refresh at /oauth/token
- id_token JWT decoding to extract https://api.openai.com/auth.chatgpt_account_id
- token persistence in ~/.codex/auth.json and local backups
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import socketserver
import sys
import threading
import time
from typing import Any
import urllib.parse
import webbrowser

import requests

from jarvis.utils import logging as log

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
PORT = 1455
REDIRECT_URI = f"http://localhost:{PORT}/auth/callback"
SCOPE = "openid profile email offline_access"
BACKEND_ENDPOINT = "https://chatgpt.com/backend-api/codex"

CODEX_DIR = Path.home() / ".codex"
PRIMARY_AUTH_PATH = CODEX_DIR / "auth.json"
FALLBACK_AUTH_PATH = Path(__file__).resolve().parent.parent.parent / ".codex_tokens.json"


def generate_pkce() -> tuple[str, str]:
    """Generate (code_verifier, code_challenge) using S256."""
    code_verifier = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(hashed).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


def decode_id_token(id_token: str) -> dict[str, Any]:
    """Decode an unverified JWT payload."""
    if not id_token or "." not in id_token:
        return {}
    parts = id_token.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1]
    rem = len(payload_b64) % 4
    if rem > 0:
        payload_b64 += "=" * (4 - rem)
    try:
        data = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        return json.loads(data.decode("utf-8"))
    except Exception as exc:
        log.warning(f"Failed to decode id_token: {exc}")
        return {}


def extract_chatgpt_account_id(id_token_or_payload: str | dict[str, Any]) -> str:
    """Extract https://api.openai.com/auth.chatgpt_account_id from id_token claims."""
    if isinstance(id_token_or_payload, str):
        payload = decode_id_token(id_token_or_payload)
    else:
        payload = id_token_or_payload

    # Format 1: auth claim object {"https://api.openai.com/auth": {"chatgpt_account_id": "..."}}
    auth_claim = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        acc = auth_claim.get("chatgpt_account_id")
        if acc:
            return str(acc).strip()

    # Format 2: flattened key "https://api.openai.com/auth.chatgpt_account_id"
    literal = payload.get("https://api.openai.com/auth.chatgpt_account_id")
    if literal:
        return str(literal).strip()

    # Format 3: direct fallback keys
    for k in ("chatgpt_account_id", "account_id"):
        v = payload.get(k)
        if v:
            return str(v).strip()

    return ""


def get_token_expiration(access_token: str) -> float:
    """Read exp timestamp from access_token JWT payload."""
    payload = decode_id_token(access_token)
    if "exp" in payload:
        try:
            return float(payload["exp"])
        except (ValueError, TypeError):
            pass
    return 0.0


def load_tokens() -> dict[str, Any]:
    """Load persisted Codex OAuth tokens from primary or fallback file."""
    for path in (PRIMARY_AUTH_PATH, FALLBACK_AUTH_PATH):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                tokens_obj = data.get("tokens", {}) if isinstance(data.get("tokens"), dict) else {}

                access_token = (
                    tokens_obj.get("access_token")
                    or data.get("access_token")
                    or os.environ.get("CODEX_ACCESS_TOKEN")
                )
                refresh_token = (
                    tokens_obj.get("refresh_token")
                    or data.get("refresh_token")
                    or os.environ.get("CODEX_REFRESH_TOKEN")
                )
                id_token = (
                    tokens_obj.get("id_token")
                    or data.get("id_token")
                    or os.environ.get("CODEX_ID_TOKEN")
                )
                account_id = (
                    tokens_obj.get("account_id")
                    or data.get("chatgpt_account_id")
                    or data.get("account_id")
                    or (extract_chatgpt_account_id(id_token) if id_token else "")
                    or os.environ.get("CHATGPT_ACCOUNT_ID")
                )
                expires_at = data.get("expires_at", 0.0)
                if not expires_at and access_token:
                    expires_at = get_token_expiration(access_token)

                if access_token:
                    return {
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "id_token": id_token,
                        "chatgpt_account_id": account_id,
                        "expires_at": expires_at,
                        "source": str(path),
                        "_raw": data,
                    }
            except Exception as exc:
                log.warning(f"Failed to read Codex tokens from {path}: {exc}")

    # Fallback to environment variables if present
    env_access = os.environ.get("CODEX_ACCESS_TOKEN")
    if env_access:
        env_refresh = os.environ.get("CODEX_REFRESH_TOKEN", "")
        env_id = os.environ.get("CODEX_ID_TOKEN", "")
        env_acc = os.environ.get("CHATGPT_ACCOUNT_ID") or (extract_chatgpt_account_id(env_id) if env_id else "")
        return {
            "access_token": env_access,
            "refresh_token": env_refresh,
            "id_token": env_id,
            "chatgpt_account_id": env_acc,
            "expires_at": get_token_expiration(env_access),
            "source": "environment",
            "_raw": {},
        }

    return {}


def save_tokens(tokens: dict[str, Any]) -> None:
    """Save tokens to ~/.codex/auth.json and fallback location."""
    CODEX_DIR.mkdir(parents=True, exist_ok=True)

    # Read existing auth.json to merge fields smoothly
    existing: dict[str, Any] = {}
    if PRIMARY_AUTH_PATH.exists():
        try:
            existing = json.loads(PRIMARY_AUTH_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token") or existing.get("tokens", {}).get("refresh_token") or existing.get("refresh_token", "")
    id_token = tokens.get("id_token") or existing.get("tokens", {}).get("id_token") or existing.get("id_token", "")
    account_id = tokens.get("chatgpt_account_id") or existing.get("tokens", {}).get("account_id") or existing.get("chatgpt_account_id", "")
    if not account_id and id_token:
        account_id = extract_chatgpt_account_id(id_token)

    expires_at = tokens.get("expires_at", 0.0)
    if not expires_at and access_token:
        expires_at = get_token_expiration(access_token)

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime())

    tokens_dict = existing.get("tokens", {}) if isinstance(existing.get("tokens"), dict) else {}
    tokens_dict["access_token"] = access_token
    if refresh_token:
        tokens_dict["refresh_token"] = refresh_token
    if id_token:
        tokens_dict["id_token"] = id_token
    if account_id:
        tokens_dict["account_id"] = account_id

    auth_json_data = {
        **existing,
        "auth_mode": existing.get("auth_mode", "chatgpt"),
        "tokens": tokens_dict,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "chatgpt_account_id": account_id,
        "expires_at": expires_at,
        "last_refresh": now_iso,
    }

    try:
        PRIMARY_AUTH_PATH.write_text(json.dumps(auth_json_data, indent=2), encoding="utf-8")
        log.info(f"Codex tokens saved to {PRIMARY_AUTH_PATH}")
    except Exception as exc:
        log.warning(f"Could not write to {PRIMARY_AUTH_PATH}: {exc}")

    # Also update fallback file in Jarvis directory
    try:
        FALLBACK_AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        fallback_data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
            "chatgpt_account_id": account_id,
            "expires_at": expires_at,
            "last_refresh": now_iso,
        }
        FALLBACK_AUTH_PATH.write_text(json.dumps(fallback_data, indent=2), encoding="utf-8")
    except Exception as exc:
        log.debug(f"Could not write fallback token file: {exc}")


def logout() -> bool:
    """Log out of OpenAI Codex by removing stored credentials and tokens."""
    removed = False
    for path in (PRIMARY_AUTH_PATH, FALLBACK_AUTH_PATH):
        if path.exists():
            try:
                path.unlink()
                removed = True
                log.info(f"Removed credentials from {path}")
            except Exception as exc:
                log.warning(f"Failed to remove {path}: {exc}")

    # Clear environment variables in current process if set
    for env_var in ("CODEX_ACCESS_TOKEN", "CODEX_REFRESH_TOKEN", "CODEX_ID_TOKEN", "CHATGPT_ACCOUNT_ID"):
        if env_var in os.environ:
            del os.environ[env_var]
            removed = True

    return removed


def refresh_tokens(refresh_token: str | None = None) -> dict[str, Any]:
    """Refresh tokens by POSTing grant_type=refresh_token to /oauth/token."""
    current = load_tokens()
    tok = refresh_token or current.get("refresh_token")
    if not tok:
        raise ValueError("No refresh token available to refresh.")

    url = f"{ISSUER}/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": tok,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "OpenAI-Codex-CLI/0.153.4",
    }

    log.info("Refreshing OpenAI Codex session tokens...")
    resp = requests.post(url, data=payload, headers=headers, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Token refresh failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    new_access = data.get("access_token")
    if not new_access:
        raise RuntimeError("OAuth token endpoint did not return an access_token.")

    updated = {
        "access_token": new_access,
        "refresh_token": data.get("refresh_token") or tok,
        "id_token": data.get("id_token") or current.get("id_token", ""),
        "chatgpt_account_id": current.get("chatgpt_account_id", ""),
    }

    if "id_token" in data:
        acc_id = extract_chatgpt_account_id(data["id_token"])
        if acc_id:
            updated["chatgpt_account_id"] = acc_id

    if "expires_in" in data:
        updated["expires_at"] = time.time() + float(data["expires_in"])
    else:
        updated["expires_at"] = get_token_expiration(new_access)

    save_tokens(updated)
    log.success("OpenAI Codex tokens refreshed successfully.")
    return updated


def exchange_code_for_tokens(code: str, code_verifier: str) -> dict[str, Any]:
    """Exchange authorization code for access, refresh, and id tokens at /oauth/token."""
    url = f"{ISSUER}/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "OpenAI-Codex-CLI/0.153.4",
    }

    log.info("Exchanging authorization code for tokens...")
    resp = requests.post(url, data=payload, headers=headers, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    id_token = data.get("id_token", "")
    expires_in = data.get("expires_in", 3600)

    if not access_token:
        raise RuntimeError("No access_token returned by token exchange endpoint.")

    chatgpt_account_id = extract_chatgpt_account_id(id_token) if id_token else ""

    tokens = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "chatgpt_account_id": chatgpt_account_id,
        "expires_at": time.time() + float(expires_in),
    }

    save_tokens(tokens)
    return tokens


def build_authorize_url(code_challenge: str, state: str) -> str:
    """Build the OpenAI OAuth authorize URL with PKCE and extra parameters."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    return f"{ISSUER}/oauth/authorize?{urllib.parse.urlencode(params)}"


_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Jarvis - Sign in Successful</title>
  <style>
    body {
      margin: 0;
      padding: 0;
      background: #0d1117;
      color: #e6edf3;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
    }
    .card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 40px;
      max-width: 460px;
      text-align: center;
      box-shadow: 0 16px 32px rgba(0, 0, 0, 0.4);
    }
    .icon {
      width: 56px;
      height: 56px;
      background: #238636;
      color: white;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
      margin: 0 auto 20px;
    }
    h1 {
      font-size: 24px;
      margin: 0 0 12px;
      color: #58a6ff;
    }
    p {
      color: #8b949e;
      font-size: 15px;
      line-height: 1.5;
      margin: 0 0 24px;
    }
    .badge {
      display: inline-block;
      padding: 6px 12px;
      background: #21262d;
      border: 1px solid #30363d;
      border-radius: 6px;
      font-size: 13px;
      color: #7ee787;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✓</div>
    <h1>Authentication Successful</h1>
    <p>Jarvis has been linked to your ChatGPT Codex subscription session. You can now close this tab and return to Jarvis.</p>
    <div class="badge">OpenAI Codex CLI • Active</div>
  </div>
  <script>
    setTimeout(() => { try { window.close(); } catch(e){} }, 3000);
  </script>
</body>
</html>
"""

_ERROR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Jarvis - Authentication Error</title>
  <style>
    body {
      background: #0d1117;
      color: #f85149;
      font-family: sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
    }
    .card {
      background: #161b22;
      border: 1px solid #da3633;
      border-radius: 12px;
      padding: 32px;
      max-width: 440px;
      text-align: center;
    }
  </style>
</head>
<body>
  <div class="card">
    <h2>Authentication Failed</h2>
    <p>{error}</p>
  </div>
</body>
</html>
"""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        code = qs.get("code", [None])[0]
        state = qs.get("state", [None])[0]
        error = qs.get("error", [None])[0]
        error_desc = qs.get("error_description", [None])[0]

        if error:
            self.server.error = f"{error}: {error_desc or 'Unknown error'}"
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_ERROR_HTML.format(error=self.server.error).encode("utf-8"))
            self.server.done_event.set()
            return

        if state != self.server.expected_state:
            self.server.error = "State mismatch (possible CSRF attack)."
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_ERROR_HTML.format(error=self.server.error).encode("utf-8"))
            self.server.done_event.set()
            return

        if not code:
            self.server.error = "Missing code in OAuth callback."
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_ERROR_HTML.format(error=self.server.error).encode("utf-8"))
            self.server.done_event.set()
            return

        self.server.code = code
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_SUCCESS_HTML.encode("utf-8"))
        self.server.done_event.set()

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy HTTP request logging to console
        pass


class _CallbackServer(http.server.HTTPServer):
    def __init__(self, server_address: tuple[str, int], expected_state: str):
        super().__init__(server_address, _CallbackHandler)
        self.expected_state = expected_state
        self.code: str | None = None
        self.error: str | None = None
        self.done_event = threading.Event()


def login(open_browser: bool = True, timeout_seconds: int = 180) -> dict[str, Any]:
    """Execute the full Authorization Code + PKCE (S256) flow."""
    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    auth_url = build_authorize_url(code_challenge, state)

    log.info(f"Starting local OAuth listener on http://localhost:{PORT}/auth/callback")
    try:
        server = _CallbackServer(("127.0.0.1", PORT), expected_state=state)
    except OSError as exc:
        raise RuntimeError(
            f"Could not bind local callback server to port {PORT}. Is another instance running? ({exc})"
        )

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    log.jarvis(f"🔐 Opening browser to sign in with ChatGPT: {auth_url}")
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception as exc:
            log.warning(f"Could not automatically open browser: {exc}")
            print(f"\nPlease open this URL manually in your browser:\n{auth_url}\n")
    else:
        print(f"\nPlease open this URL manually in your browser:\n{auth_url}\n")

    log.info(f"Waiting up to {timeout_seconds}s for ChatGPT OAuth callback...")
    completed = server.done_event.wait(timeout=timeout_seconds)

    server.shutdown()
    server.server_close()

    if not completed:
        raise TimeoutError("OAuth login timed out waiting for browser callback.")

    if server.error:
        raise RuntimeError(f"OAuth authentication error: {server.error}")

    if not server.code:
        raise RuntimeError("No authorization code captured from callback.")

    tokens = exchange_code_for_tokens(server.code, code_verifier)
    log.success("OpenAI Codex login successful!")
    if tokens.get("chatgpt_account_id"):
        log.info(f"ChatGPT Account ID: {tokens['chatgpt_account_id']}")
    return tokens


def get_valid_token(force_refresh: bool = False) -> tuple[str, str]:
    """Return (access_token, chatgpt_account_id).

    Proactively refreshes the token if expired or close to expiry (< 5 minutes).
    Triggers an interactive login flow if no tokens or refresh token exist.
    """
    tokens = load_tokens()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_at = tokens.get("expires_at", 0.0)
    account_id = tokens.get("chatgpt_account_id") or ""

    now = time.time()
    # If no token at all, login
    if not access_token:
        log.info("No existing OpenAI Codex session found. Launching Sign in with ChatGPT...")
        tokens = login()
        return tokens["access_token"], tokens.get("chatgpt_account_id", "")

    # If force_refresh or near expiry (within 300 seconds)
    is_expiring = expires_at and (now >= expires_at - 300)
    if force_refresh or is_expiring:
        if refresh_token:
            try:
                log.info("Codex access token expiring soon; refreshing...")
                tokens = refresh_tokens(refresh_token)
                return tokens["access_token"], tokens.get("chatgpt_account_id") or account_id
            except Exception as exc:
                log.warning(f"Token refresh failed: {exc}. Re-authenticating...")
                tokens = login()
                return tokens["access_token"], tokens.get("chatgpt_account_id", "")
        else:
            log.info("No refresh token present. Re-authenticating...")
            tokens = login()
            return tokens["access_token"], tokens.get("chatgpt_account_id", "")

    return access_token, account_id


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "login":
        print("Starting login flow...")
        res = login()
        print(f"Logged in successfully. Account ID: {res.get('chatgpt_account_id')}")
    elif action == "refresh":
        print("Refreshing tokens...")
        res = refresh_tokens()
        print(f"Refreshed successfully. Account ID: {res.get('chatgpt_account_id')}")
    elif action == "logout":
        if logout():
            print("Successfully logged out and removed stored tokens.")
        else:
            print("No active tokens found.")
    else:
        tokens = load_tokens()
        if tokens.get("access_token"):
            exp_in = tokens.get("expires_at", 0.0) - time.time()
            print(f"Status: Authenticated ({tokens.get('source')})")
            print(f"Account ID: {tokens.get('chatgpt_account_id')}")
            print(f"Expires in: {int(exp_in)} seconds (~{int(exp_in/3600)} hours)")
        else:
            print("Status: Not authenticated. Run with 'login' to sign in.")
