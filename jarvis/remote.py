"""Secure, opt-in remote-device support for Jarvis.

This module deliberately treats the hosted service as an untrusted relay.  A
device generates its own X25519 and Ed25519 keys, exchanges only public keys
during an explicit one-time-code pairing, and keeps the derived secret in a
local state directory.  Commands are sent as authenticated encrypted envelopes
and are only executed by a Jarvis agent that the owner started on the other
machine.

There is no listener on a user's computer and no endpoint that accepts an
arbitrary unauthenticated desktop command.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

import requests

if TYPE_CHECKING:  # pragma: no cover - avoids a runtime import cycle
    from .config import Config


PROTOCOL = "jarvis-remote-v1"
STATE_VERSION = 1
MAX_TASK_CHARS = 8_000
MAX_RESULT_CHARS = 10_000


class RemoteError(RuntimeError):
    """A relay, pairing, or local remote-state error."""


class RemoteSecurityError(RemoteError):
    """An envelope failed cryptographic validation."""


def _crypto():
    """Load the optional-at-import-time crypto dependency.

    Keeping this lazy means Jarvis's ordinary local-only startup remains
    usable long enough to explain exactly what installation is required.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as exc:  # pragma: no cover - environment specific
        raise RemoteError("Remote devices need the 'cryptography' package. "
                          "Run: pip install -r requirements.txt") from exc
    return (hashes, serialization, Ed25519PrivateKey, Ed25519PublicKey,
            X25519PrivateKey, X25519PublicKey, ChaCha20Poly1305, HKDF)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(value: str, *, field: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise RemoteSecurityError(f"{field} is not valid base64") from exc


def _canonical_message(pair_id: str, sender: str, recipient: str,
                       nonce: str, ciphertext: str) -> bytes:
    """Keep byte-for-byte in sync with ``relay_server.main``."""
    return "|".join((PROTOCOL, pair_id, sender, recipient,
                     nonce, ciphertext)).encode("utf-8")


@dataclass(frozen=True)
class Identity:
    """One ephemeral pairing identity. Private keys never go to the relay."""

    kx_private: str
    kx_public: str
    sign_private: str
    sign_public: str


def create_identity() -> Identity:
    hashes, serialization, EdPrivate, _EdPublic, XPrivate, _XPublic, _Cipher, _HKDF = _crypto()
    del hashes  # the names are returned together to keep this loader compact
    kx = XPrivate.generate()
    signing = EdPrivate.generate()
    raw = serialization.Encoding.Raw
    private_format = serialization.PrivateFormat.Raw
    public_format = serialization.PublicFormat.Raw
    no_encryption = serialization.NoEncryption()
    return Identity(
        kx_private=_b64(kx.private_bytes(raw, private_format, no_encryption)),
        kx_public=_b64(kx.public_key().public_bytes(raw, public_format)),
        sign_private=_b64(signing.private_bytes(raw, private_format, no_encryption)),
        sign_public=_b64(signing.public_key().public_bytes(raw, public_format)),
    )


def derive_secret(private_key: str, peer_public_key: str, pair_id: str) -> bytes:
    hashes, _serialization, _EdPrivate, _EdPublic, XPrivate, XPublic, _Cipher, HKDF = _crypto()
    try:
        private = XPrivate.from_private_bytes(_unb64(private_key, field="private key"))
        public = XPublic.from_public_bytes(_unb64(peer_public_key, field="peer public key"))
        shared = private.exchange(public)
    except Exception as exc:
        raise RemoteSecurityError("pairing key exchange failed") from exc
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=(PROTOCOL + ":" + pair_id).encode("utf-8")).derive(shared)


def fingerprint(secret: bytes) -> str:
    """Human-comparable fingerprint; it is not itself a secret."""
    raw = hashlib.sha256(b"Jarvis Remote fingerprint\0" + secret).hexdigest().upper()[:20]
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def _aad(pair_id: str, sender: str, recipient: str) -> bytes:
    return (PROTOCOL + "|" + pair_id + "|" + sender + "|" + recipient).encode("utf-8")


def encrypt_payload(secret: bytes, signing_private: str, pair_id: str,
                    sender: str, recipient: str, payload: dict[str, Any]) -> dict[str, str]:
    _hashes, _serialization, EdPrivate, _EdPublic, _XPrivate, _XPublic, Cipher, _HKDF = _crypto()
    if sender not in {"controller", "agent"} or recipient not in {"controller", "agent"} or sender == recipient:
        raise RemoteSecurityError("invalid message direction")
    try:
        plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RemoteError("remote payload is not JSON serializable") from exc
    if len(plaintext) > MAX_RESULT_CHARS + 2_000:
        raise RemoteError("remote payload is too large")
    nonce_raw = os.urandom(12)
    nonce = _b64(nonce_raw)
    ciphertext = _b64(Cipher(secret).encrypt(nonce_raw, plaintext, _aad(pair_id, sender, recipient)))
    try:
        signing_key = EdPrivate.from_private_bytes(_unb64(signing_private, field="signing key"))
        signature = signing_key.sign(_canonical_message(pair_id, sender, recipient, nonce, ciphertext))
    except Exception as exc:
        raise RemoteSecurityError("could not sign remote message") from exc
    return {"nonce": nonce, "ciphertext": ciphertext, "signature": _b64(signature)}


def decrypt_payload(secret: bytes, peer_signing_public: str, pair_id: str,
                    sender: str, recipient: str, envelope: dict[str, Any]) -> dict[str, Any]:
    _hashes, _serialization, _EdPrivate, EdPublic, _XPrivate, _XPublic, Cipher, _HKDF = _crypto()
    if sender not in {"controller", "agent"} or recipient not in {"controller", "agent"} or sender == recipient:
        raise RemoteSecurityError("invalid message direction")
    try:
        nonce = str(envelope["nonce"])
        ciphertext = str(envelope["ciphertext"])
        signature = str(envelope["signature"])
        signing_key = EdPublic.from_public_bytes(_unb64(peer_signing_public, field="peer signing key"))
        signing_key.verify(_unb64(signature, field="message signature"),
                           _canonical_message(pair_id, sender, recipient, nonce, ciphertext))
        data = Cipher(secret).decrypt(_unb64(nonce, field="message nonce"),
                                      _unb64(ciphertext, field="message ciphertext"),
                                      _aad(pair_id, sender, recipient))
        payload = json.loads(data.decode("utf-8"))
    except RemoteSecurityError:
        raise
    except Exception as exc:
        raise RemoteSecurityError("remote message did not pass authentication") from exc
    if not isinstance(payload, dict):
        raise RemoteSecurityError("remote payload must be an object")
    return payload


@dataclass
class Pairing:
    label: str
    endpoint: str
    pair_id: str
    role: str
    peer_name: str
    local_name: str
    secret: str
    sign_private: str
    peer_sign_public: str
    trusted: bool = False
    received_sequence: int = 0
    inbox: list[dict[str, Any]] = field(default_factory=list)
    processed_message_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def peer_role(self) -> str:
        return "agent" if self.role == "controller" else "controller"

    @property
    def secret_bytes(self) -> bytes:
        return _unb64(self.secret, field="stored pairing secret")

    @property
    def verification_fingerprint(self) -> str:
        return fingerprint(self.secret_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label, "endpoint": self.endpoint, "pair_id": self.pair_id,
            "role": self.role, "peer_name": self.peer_name, "local_name": self.local_name,
            "secret": self.secret, "sign_private": self.sign_private,
            "peer_sign_public": self.peer_sign_public, "trusted": self.trusted,
            "received_sequence": self.received_sequence, "inbox": self.inbox[-50:],
            "processed_message_ids": self.processed_message_ids[-200:],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pairing":
        role = str(data.get("role", ""))
        if role not in {"controller", "agent"}:
            raise RemoteError("stored pairing has an invalid role")
        required = ("label", "endpoint", "pair_id", "peer_name", "local_name", "secret",
                    "sign_private", "peer_sign_public")
        if any(not isinstance(data.get(key), str) or not data[key] for key in required):
            raise RemoteError("stored pairing is incomplete")
        inbox = data.get("inbox", [])
        return cls(
            label=str(data["label"]), endpoint=str(data["endpoint"]), pair_id=str(data["pair_id"]),
            role=role, peer_name=str(data["peer_name"]), local_name=str(data["local_name"]),
            secret=str(data["secret"]), sign_private=str(data["sign_private"]),
            peer_sign_public=str(data["peer_sign_public"]), trusted=bool(data.get("trusted", False)),
            received_sequence=max(0, int(data.get("received_sequence", 0) or 0)),
            inbox=inbox if isinstance(inbox, list) else [],
            processed_message_ids=[str(x) for x in data.get("processed_message_ids", [])
                                   if isinstance(x, str)][-200:],
            created_at=float(data.get("created_at", time.time()) or time.time()),
        )


class PairingStore:
    """Local secret storage, one file per Windows/user account by default."""

    def __init__(self, state_dir: str = "") -> None:
        raw = state_dir or os.getenv("JARVIS_REMOTE_STATE_DIR", "")
        self.directory = Path(raw).expanduser() if raw else Path.home() / ".jarvis_remote"
        self.path = self.directory / "pairings.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": STATE_VERSION, "pairings": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RemoteError(f"could not read local pairing state {self.path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("pairings", []), list):
            raise RemoteError(f"local pairing state {self.path} is invalid")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        try:
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:  # Windows permissions are managed by the ACL.
                pass
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass

    def list(self, role: str | None = None) -> list[Pairing]:
        result: list[Pairing] = []
        for item in self._read()["pairings"]:
            try:
                pair = Pairing.from_dict(item)
            except RemoteError:
                continue
            if role is None or pair.role == role:
                result.append(pair)
        return result

    def get(self, label: str, role: str | None = None) -> Pairing:
        wanted = label.strip().casefold()
        for pair in self.list(role):
            if pair.label.casefold() == wanted:
                return pair
        suffix = " remote device" if role == "controller" else " pairing"
        raise RemoteError(f"no{suffix} named '{label}'. Use :remote list to see paired devices.")

    def save(self, pairing: Pairing) -> None:
        data = self._read()
        values = data["pairings"]
        replacement = pairing.to_dict()
        for i, item in enumerate(values):
            if (str(item.get("label", "")).casefold() == pairing.label.casefold()
                    and item.get("role") == pairing.role):
                values[i] = replacement
                self._write(data)
                return
        values.append(replacement)
        self._write(data)


def _endpoint(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    parsed = urlparse(endpoint)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RemoteError("remote.relay_url must be a complete https:// relay URL")
    if parsed.scheme != "https" and (parsed.hostname or "").lower() not in local_hosts:
        raise RemoteError("the remote relay must use HTTPS (http is allowed only for localhost)")
    return endpoint


def _device_name(value: str = "") -> str:
    name = value.strip() or socket.gethostname().strip() or "Jarvis device"
    if len(name) > 80:
        raise RemoteError("device name must be 80 characters or fewer")
    return name


class RelayClient:
    """Small typed wrapper around the hosted relay API."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = _endpoint(endpoint)

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 params: dict | None = None, timeout: float = 30) -> dict[str, Any]:
        try:
            response = requests.request(method, self.endpoint + path, json=json_body,
                                        params=params, timeout=(5, timeout + 10))
        except requests.RequestException as exc:
            raise RemoteError(f"could not reach remote relay: {exc}") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not response.ok:
            detail = data.get("detail") if isinstance(data, dict) else ""
            raise RemoteError(f"relay returned {response.status_code}: {detail or response.text[:160]}")
        if not isinstance(data, dict):
            raise RemoteError("relay returned an invalid response")
        return data

    def start_pairing(self, name: str, identity: Identity) -> dict[str, Any]:
        return self._request("POST", "/v1/pairings", json_body={
            "name": name, "kx_public": identity.kx_public, "sign_public": identity.sign_public,
        })

    def claim_pairing(self, code: str, name: str, identity: Identity) -> dict[str, Any]:
        return self._request("POST", "/v1/pairings/claim", json_body={
            "code": code.strip().upper(), "name": name,
            "kx_public": identity.kx_public, "sign_public": identity.sign_public,
        })

    def pairing_status(self, pair_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/pairings/{pair_id}")

    def send(self, pairing: Pairing, payload: dict[str, Any]) -> int:
        _require_trusted(pairing)
        envelope = encrypt_payload(pairing.secret_bytes, pairing.sign_private, pairing.pair_id,
                                   pairing.role, pairing.peer_role, payload)
        data = self._request("POST", f"/v1/pairs/{pairing.pair_id}/messages",
                             json_body={"sender": pairing.role, "envelope": envelope})
        return int(data.get("sequence", 0) or 0)

    def receive(self, pairing: Pairing, *, timeout: int = 20) -> list[dict[str, Any]]:
        _require_trusted(pairing)
        timeout = max(0, min(30, int(timeout)))
        data = self._request("GET", f"/v1/pairs/{pairing.pair_id}/messages",
                             params={"recipient": pairing.role,
                                     "after": pairing.received_sequence, "timeout": timeout},
                             timeout=timeout + 5)
        messages = data.get("messages", [])
        if not isinstance(messages, list):
            raise RemoteError("relay sent an invalid message list")
        received: list[dict[str, Any]] = []
        highest = pairing.received_sequence
        for message in sorted(messages, key=lambda item: int(item.get("sequence", 0))):
            if not isinstance(message, dict):
                continue
            sequence = int(message.get("sequence", 0) or 0)
            if sequence <= pairing.received_sequence:
                continue
            highest = max(highest, sequence)
            if message.get("sender") != pairing.peer_role or message.get("recipient") != pairing.role:
                continue
            try:
                received.append(decrypt_payload(pairing.secret_bytes, pairing.peer_sign_public,
                                                pairing.pair_id, pairing.peer_role, pairing.role,
                                                message))
            except RemoteSecurityError:
                # A bad/replayed message is acknowledged so it cannot wedge the
                # device's long-poll loop. It was never executed.
                continue
        if highest > pairing.received_sequence:
            pairing.received_sequence = highest
        return received


@dataclass(frozen=True)
class PairingOffer:
    label: str
    local_name: str
    endpoint: str
    pair_id: str
    code: str
    expires_at: float
    identity: Identity


def start_pairing(cfg: "Config", label: str, *, local_name: str = "") -> PairingOffer:
    label = label.strip()
    if not label or len(label) > 80:
        raise RemoteError("choose a remote-device name of 1 to 80 characters")
    endpoint = _endpoint(cfg.remote.relay_url)
    identity = create_identity()
    local_name = _device_name(local_name)
    created = RelayClient(endpoint).start_pairing(local_name, identity)
    pair_id, code = str(created.get("pair_id", "")), str(created.get("code", ""))
    if not pair_id or not code:
        raise RemoteError("relay did not return a pairing code")
    return PairingOffer(label, local_name, endpoint, pair_id, code,
                        float(created.get("expires_at", time.time())), identity)


def finish_pairing(cfg: "Config", offer: PairingOffer, *, poll_seconds: int = 2,
                   deadline: float | None = None) -> Pairing:
    """Wait for the other device to claim an offer and save it untrusted."""
    deadline = deadline or offer.expires_at
    client = RelayClient(offer.endpoint)
    while time.time() < deadline:
        status = client.pairing_status(offer.pair_id)
        agent = status.get("agent")
        if isinstance(agent, dict):
            peer_kx = str(agent.get("kx_public", ""))
            peer_sign = str(agent.get("sign_public", ""))
            peer_name = _device_name(str(agent.get("name", "")))
            secret = derive_secret(offer.identity.kx_private, peer_kx, offer.pair_id)
            pairing = Pairing(label=offer.label, endpoint=offer.endpoint, pair_id=offer.pair_id,
                              role="controller", peer_name=peer_name, local_name=offer.local_name,
                              secret=_b64(secret), sign_private=offer.identity.sign_private,
                              peer_sign_public=peer_sign)
            PairingStore(cfg.remote.state_dir).save(pairing)
            return pairing
        time.sleep(max(0.25, poll_seconds))
    raise RemoteError("pairing code expired before the other device accepted it")


def accept_pairing(cfg: "Config", code: str, *, local_name: str = "") -> Pairing:
    endpoint = _endpoint(cfg.remote.relay_url)
    identity = create_identity()
    local_name = _device_name(local_name)
    status = RelayClient(endpoint).claim_pairing(code, local_name, identity)
    controller = status.get("controller")
    if not isinstance(controller, dict):
        raise RemoteError("relay did not return controller public keys")
    peer_name = _device_name(str(controller.get("name", "")))
    pair_id = str(status.get("pair_id", ""))
    if not pair_id:
        raise RemoteError("relay did not return a pairing id")
    secret = derive_secret(identity.kx_private, str(controller.get("kx_public", "")), pair_id)
    pairing = Pairing(label=peer_name, endpoint=endpoint, pair_id=pair_id, role="agent",
                      peer_name=peer_name, local_name=local_name, secret=_b64(secret),
                      sign_private=identity.sign_private,
                      peer_sign_public=str(controller.get("sign_public", "")))
    PairingStore(cfg.remote.state_dir).save(pairing)
    return pairing


def trust_pairing(cfg: "Config", label: str, supplied_fingerprint: str,
                  *, role: str = "controller") -> Pairing:
    store = PairingStore(cfg.remote.state_dir)
    pairing = store.get(label, role=role)
    expected = pairing.verification_fingerprint.replace("-", "").casefold()
    supplied = supplied_fingerprint.replace("-", "").strip().casefold()
    if not secrets.compare_digest(expected, supplied):
        raise RemoteSecurityError("fingerprint does not match this pairing; do not trust it")
    pairing.trusted = True
    store.save(pairing)
    return pairing


def _require_trusted(pairing: Pairing) -> None:
    if not pairing.trusted:
        raise RemoteSecurityError(
            f"'{pairing.label}' is paired but not trusted. Compare the fingerprint on both "
            "devices, then run --remote-trust on each device before sending commands.")


def _take_inbox(pairing: Pairing, request_id: str) -> dict[str, Any] | None:
    for i, message in enumerate(pairing.inbox):
        if message.get("type") == "task_result" and message.get("task_id") == request_id:
            return pairing.inbox.pop(i)
    return None


def _already_processed(pairing: Pairing, message: dict[str, Any]) -> bool:
    """Persist task IDs before execution to make relay replays harmless.

    Sequence cursors protect against ordinary redelivery.  This second guard
    matters because an untrusted relay could replay an old, validly signed
    envelope under a new sequence number.  Only task messages are executable,
    and their sender-generated id is unique for a pairing.
    """
    message_id = str(message.get("id", "")).strip()
    if not message_id:
        return True
    marker = "task:" + message_id
    if marker in pairing.processed_message_ids:
        return True
    pairing.processed_message_ids.append(marker)
    pairing.processed_message_ids = pairing.processed_message_ids[-200:]
    return False


def send_task(cfg: "Config", label: str, task: str, *, timeout: int | None = None) -> tuple[bool, str]:
    """Send a natural-language task and wait for that device's final result."""
    task = task.strip()
    if not task:
        return False, "remote task needs a task description"
    if len(task) > MAX_TASK_CHARS:
        return False, f"remote task is too long (limit {MAX_TASK_CHARS} characters)"
    store = PairingStore(cfg.remote.state_dir)
    pairing = store.get(label, role="controller")
    _require_trusted(pairing)
    request_id = secrets.token_urlsafe(18)
    client = RelayClient(pairing.endpoint)
    client.send(pairing, {"type": "task", "id": request_id, "task": task,
                          "sent_at": int(time.time())})
    wait_seconds = timeout if timeout is not None else cfg.remote.result_timeout
    try:
        wait_seconds = max(5, min(600, int(wait_seconds)))
    except (TypeError, ValueError):
        wait_seconds = 180
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        result = _take_inbox(pairing, request_id)
        if result is not None:
            store.save(pairing)
            ok = bool(result.get("ok", False))
            text = str(result.get("result", "remote device returned no result"))
            return ok, f"{pairing.label}: {text[:MAX_RESULT_CHARS]}"
        remaining = deadline - time.monotonic()
        messages = client.receive(pairing, timeout=min(20, max(0, int(remaining))))
        for message in messages:
            if message.get("type") == "task_result" and message.get("task_id") == request_id:
                store.save(pairing)  # save sequence before returning: no replay on restart
                ok = bool(message.get("ok", False))
                text = str(message.get("result", "remote device returned no result"))
                return ok, f"{pairing.label}: {text[:MAX_RESULT_CHARS]}"
            pairing.inbox.append(message)
            pairing.inbox = pairing.inbox[-50:]
        store.save(pairing)
    return False, (f"{pairing.label} did not finish within {wait_seconds}s. It may be offline, "
                   "awaiting a local confirmation, or still working.")


def _configure_remote_confirmation(cfg: "Config", allow_unattended: bool) -> bool:
    """Apply the target device's confirmation policy and return its mode."""
    unattended = allow_unattended or not cfg.remote.require_confirmation
    # Remote policy wins over the ordinary desktop preference: a target
    # configured for unattended operation must not inherit a stray local
    # ``safety.confirm_each_action: true`` and stop waiting for every click.
    cfg.safety.confirm_each_action = not unattended
    return unattended


def run_remote_agent(cfg: "Config", *, allow_unattended: bool = False,
                     printer: Callable[[str], None] = print) -> int:
    """Run the opt-in remote agent on a paired device until Ctrl+C.

    Trusted remote tasks run without local prompts by default. Set
    ``remote.require_confirmation: true`` on a particular device to restore
    per-action approval; ``--remote-allow-unattended`` still overrides that
    setting for a single launch.
    """
    store = PairingStore(cfg.remote.state_dir)
    pairings = [p for p in store.list(role="agent") if p.trusted]
    untrusted = [p for p in store.list(role="agent") if not p.trusted]
    if not pairings:
        if untrusted:
            raise RemoteSecurityError("pairing exists but is not trusted; compare and trust its fingerprint first")
        raise RemoteError("this device has no paired controller; accept a pairing code first")
    unattended = _configure_remote_confirmation(cfg, allow_unattended)
    from .agent.brain import BrainError, make_brain
    from .agent.loop import Agent
    from . import scheduler
    try:
        agent = Agent(make_brain(cfg.brain), cfg)
    except BrainError as exc:
        raise RemoteError(str(exc)) from exc
    mode = "UNATTENDED" if unattended else "local confirmation required"
    printer(f"Jarvis Remote agent online ({mode}); press Ctrl+C to stop.")

    def asker(question: str) -> str | None:
        try:
            return input(f"\nRemote task asks: {question}\nanswer (blank cancels) > ").strip() or None
        except (EOFError, KeyboardInterrupt):
            return None

    try:
        while True:
            handled = False
            # Reload between jobs so a newly trusted/paired controller can be
            # picked up without restarting the agent.
            pairings = [p for p in store.list(role="agent") if p.trusted]
            for pairing in pairings:
                client = RelayClient(pairing.endpoint)
                previous_sequence = pairing.received_sequence
                try:
                    messages = client.receive(pairing, timeout=2 if len(pairings) > 1 else 20)
                except RemoteError as exc:
                    printer(f"Remote relay for {pairing.peer_name} is unavailable: {exc}")
                    continue
                if messages or pairing.received_sequence != previous_sequence:
                    store.save(pairing)  # acknowledge before executing: no duplicate command after a crash
                for message in messages:
                    if message.get("type") != "task":
                        continue
                    task_id = str(message.get("id", ""))
                    task = str(message.get("task", "")).strip()
                    if not task_id or not task or len(task) > MAX_TASK_CHARS:
                        continue
                    if _already_processed(pairing, message):
                        printer(f"Ignored replayed remote task {task_id} from {pairing.peer_name}.")
                        continue
                    # Durable before execution: a crash/restart cannot turn a
                    # replayed signed envelope into a second desktop action.
                    store.save(pairing)
                    handled = True
                    printer(f"Remote task from {pairing.peer_name}: {task[:180]}")
                    try:
                        client.send(pairing, {"type": "task_started", "task_id": task_id,
                                              "started_at": int(time.time())})
                    except RemoteError as exc:
                        printer(f"Could not acknowledge remote task: {exc}")
                        continue
                    try:
                        with scheduler.desktop():
                            result = agent.run(task, asker=asker)
                        ok = not result.lower().startswith(("cancelled", "brain error", "plan reached"))
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        ok, result = False, f"remote agent error: {exc}"
                    try:
                        client.send(pairing, {"type": "task_result", "task_id": task_id,
                                              "ok": ok, "result": str(result)[:MAX_RESULT_CHARS],
                                              "finished_at": int(time.time())})
                    except RemoteError as exc:
                        # The command already ran, so never repeat it. The
                        # sender times out safely and the owner can inspect the
                        # remote computer; a later task starts fresh.
                        printer(f"Could not send remote task result: {exc}")
            if not handled:
                # The long poll already avoids a busy loop; this small pause is
                # only relevant with multiple pairings that all return quickly.
                time.sleep(0.1)
    except KeyboardInterrupt:
        printer("Jarvis Remote agent stopped.")
        return 0


def status_text(cfg: "Config", *, role: str | None = None) -> str:
    pairings = PairingStore(cfg.remote.state_dir).list(role=role)
    if not pairings:
        return "No remote devices are paired on this computer."
    lines = ["Remote pairings:"]
    for pair in pairings:
        state = "trusted" if pair.trusted else "VERIFY FINGERPRINT"
        lines.append(f"- {pair.label} ({pair.role}; peer: {pair.peer_name}; {state})")
    return "\n".join(lines)


def note(cfg: "Config") -> str:
    """Prompt context, only injected when usable paired devices exist."""
    try:
        pairings = [p for p in PairingStore(cfg.remote.state_dir).list("controller") if p.trusted]
    except RemoteError:
        return ""
    if not pairings:
        return ""
    names = ", ".join(p.label for p in pairings)
    return ("\n\n=== PAIRED REMOTE DEVICES ===\n"
            f"Trusted remote devices: {names}. When, and ONLY when, the user explicitly says "
            "to do something on one of those named devices, use remote_task with that exact "
            "device label and their requested task. It runs through the Jarvis agent on that "
            "device; never use it for a task on this computer.\n================================")


def console_command(raw: str, cfg: "Config") -> str:
    """Handle ``:remote`` without making the main console know transport details."""
    body = raw[1:].strip() if raw.startswith(":") else raw.strip()
    if body[:6].lower() == "remote":
        body = body[6:].strip()
    if not body or body.lower() == "list":
        return status_text(cfg, role="controller")
    low = body.lower()
    if low.startswith("trust "):
        parts = body[6:].strip().rsplit(" ", 1)
        if len(parts) != 2:
            return "usage: :remote trust <device name> <fingerprint>"
        pair = trust_pairing(cfg, parts[0], parts[1], role="controller")
        return f"Trusted {pair.label}. Remote tasks can now be sent to it."
    if low.startswith("send "):
        spec = body[5:].strip()
        if "|" not in spec:
            return "usage: :remote send <device name> | <task>"
        label, task = (part.strip() for part in spec.split("|", 1))
        return send_task(cfg, label, task)[1]
    return "usage: :remote [list | trust <device> <fingerprint> | send <device> | <task>]"
