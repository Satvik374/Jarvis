"""The pluggable LLM 'brain'.

A Brain takes the running message history and returns the model's next raw
reply (a JSON action string, per prompts.py). Four backends ship:

  * ``ollama``   - local, default. Talks to the Ollama server (localhost:11434).
  * ``llamacpp`` - local, via a llama.cpp / llama-cpp-python OpenAI server.
  * ``openai``   - any OpenAI-compatible endpoint (LM Studio, vLLM, OpenRouter...).
  * ``anthropic``- Claude API, useful as the strong-vision reference brain.

All backends share one interface so the rest of the app never branches on which
model is in use. Vision (sending the annotated screenshot) is supported by the
ollama and openai/anthropic backends when ``use_vision`` is on.
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from ..config import BrainConfig


class BrainError(RuntimeError):
    pass


class Brain:
    """Base interface."""

    def __init__(self, cfg: BrainConfig):
        self.cfg = cfg
        # requests.post() creates and tears down a Session for every call.
        # A Jarvis task makes several calls to the same host, so retain one
        # connection pool per calling thread.  Thread-local storage keeps the
        # foreground loop, cron runner, and speech worker from sharing the
        # mutable Session object while still reusing TCP/TLS connections.
        self._http_local = threading.local()

    def complete(self, system: str, messages: list[dict],
                 image=None) -> str:
        """Return the model's raw reply.

        ``messages`` is a list of {role, content} dicts (user/assistant).
        ``image`` is an optional PIL image for the current turn (vision mode).
        """
        raise NotImplementedError

    def warmup(self) -> None:
        """Best-effort backend warmup for interactive frontends."""

    def _http_post(self, url: str, **kwargs: Any):
        """POST through this thread's persistent requests connection pool."""
        import requests  # type: ignore

        session = getattr(self._http_local, "session", None)
        if session is None:
            session = requests.Session()
            self._http_local.session = session
        return session.post(url, **kwargs)

    # -- shared helpers ----------------------------------------------------
    @staticmethod
    def _as_images(image) -> list:
        """Normalize the ``image`` argument: None, a single PIL image, or a
        list of them -> always a list (user attachment + screenshot)."""
        if image is None:
            return []
        return list(image) if isinstance(image, (list, tuple)) else [image]

    @staticmethod
    def _png_b64(image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _prepare_vision_b64(image, max_dim: int = 1280,
                            quality: int = 80) -> tuple[str, str]:
        """Resize and JPEG-compress a screenshot for vision API calls.

        Returns ``(base64_string, mime_type)``.  A typical 1920x1080 PNG
        screenshot is 3-7 MB base64; after resizing to 1280 px and JPEG
        compression it drops to ~100-300 KB — small enough that the HTTP
        upload never times out.
        """
        w, h = image.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)))
        if image.mode not in ("RGB", "L"):    # RGBA/P attachments can't be JPEG
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


# Error-message fragments that mark a RETRYABLE failure. Trajectory analysis
# showed 21 runs (11%) killed by these: 429 quota (resets on a per-minute
# window - a 2s retry never survives it), network blips, token refresh flakes.
_TRANSIENT_MARKS = ("429", "502", "503", "timed out", "timeout", "max retries",
                    "connection", "temporarily", "overloaded", "exhausted",
                    "recitation", "refresh access token", "empty content")


def complete_with_retry(brain: "Brain", system: str, messages: list[dict],
                        image=None, tries: int = 3) -> str:
    """Self-healing brain call: transient failures are retried instead of
    killing the whole task. Known-transient errors (rate limits, network
    blips) get a bigger budget with long exponential backoff - a 429 quota
    needs ~a minute, not 2 seconds. Unknown errors keep the short retry.
    Only after every attempt fails does the error propagate."""
    attempt = 0
    while True:
        try:
            return brain.complete(system, messages, image=image)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            msg = str(exc).lower()
            transient = any(m in msg for m in _TRANSIENT_MARKS)
            budget = max(tries, 5) if transient else tries
            attempt += 1
            if attempt >= budget:
                raise
            delay = min(60, 5 * 2 ** (attempt - 1)) if transient else 2 * attempt
            from ..utils import logging as log
            log.warn(f"brain hiccup (attempt {attempt}/{budget}): {exc} "
                     f"- retrying in {delay:.0f}s")
            time.sleep(delay)


def _coalesce_roles(messages: list[dict]) -> list[dict]:
    """Merge consecutive same-role messages into one (Anthropic needs alternation)."""
    out: list[dict] = []
    for m in messages:
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] = out[-1]["content"] + "\n\n" + m["content"]
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def make_brain(cfg: BrainConfig) -> Brain:
    backend = cfg.backend.lower()
    if backend in {"gemini", "vertex"}:
        return GeminiVertexBrain(cfg)
    if backend == "ollama":
        return OllamaBrain(cfg)
    if backend in {"hf", "transformers", "local"}:
        return HFLocalBrain(cfg)
    if backend in {"llamacpp", "openai", "lmstudio", "vllm"}:
        return OpenAICompatBrain(cfg)
    if backend == "anthropic":
        return AnthropicBrain(cfg)
    raise BrainError(f"unknown brain backend: {cfg.backend}")


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #

class OllamaBrain(Brain):
    """Local models served by Ollama. The recommended default for this rig."""

    def complete(self, system, messages, image=None) -> str:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            msg = {"role": m["role"], "content": m["content"]}
            msgs.append(msg)
        imgs = self._as_images(image)
        if imgs and self.cfg.use_vision and msgs:
            msgs[-1]["images"] = [self._png_b64(i) for i in imgs]

        payload = {
            "model": self.cfg.model,
            "messages": msgs,
            "stream": False,
            "keep_alive": "24h",
            "options": {
                "temperature": self.cfg.temperature,
                "num_predict": self.cfg.max_tokens,
            },
            "format": "json",   # ask Ollama to constrain output to JSON
        }

        try:
            r = self._http_post(
                f"{self.cfg.base_url}/api/chat",
                json=payload,
                timeout=self.cfg.request_timeout,
            )
        except Exception as exc:
            raise BrainError(
                f"cannot reach Ollama at {self.cfg.base_url} ({exc}). "
                f"Is it running? Try:  ollama serve   and   "
                f"ollama pull {self.cfg.model}") from exc
        if r.status_code == 404:
            raise BrainError(
                f"Ollama has no model '{self.cfg.model}'. Run: "
                f"ollama pull {self.cfg.model}")
        r.raise_for_status()
        data = r.json()
        return (data.get("message", {}) or {}).get("content", "")


# --------------------------------------------------------------------------- #
# HuggingFace local (base model + your trained LoRA adapter, no server)
# --------------------------------------------------------------------------- #

class HFLocalBrain(Brain):
    """Run a fine-tuned model directly with transformers - the fastest way to
    try YOUR trained adapter without installing Ollama or converting to GGUF.

    Set in config.yaml:
        brain.backend: hf
        brain.model: Qwen/Qwen2.5-0.5B-Instruct        # the base
        brain.adapter_path: training/outputs/jarvis-lora  # your LoRA
    """

    _model = None
    _tokenizer = None
    _loaded_key = None

    def _ensure_loaded(self):
        key = (self.cfg.model, self.cfg.adapter_path)
        if HFLocalBrain._model is not None and HFLocalBrain._loaded_key == key:
            return
        self._redirect_hf_cache()
        import torch  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

        tok = AutoTokenizer.from_pretrained(self.cfg.adapter_path or self.cfg.model)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        dtype = torch.float16
        model = AutoModelForCausalLM.from_pretrained(
            self.cfg.model, device_map="auto", **_dtype_kwarg(dtype))
        if self.cfg.adapter_path:
            from peft import PeftModel  # type: ignore

            model = PeftModel.from_pretrained(model, self.cfg.adapter_path)
        model.eval()
        HFLocalBrain._model, HFLocalBrain._tokenizer = model, tok
        HFLocalBrain._loaded_key = key

    def complete(self, system, messages, image=None) -> str:
        self._ensure_loaded()
        import torch  # type: ignore

        model, tok = HFLocalBrain._model, HFLocalBrain._tokenizer
        msgs = [{"role": "system", "content": system}]
        msgs += [{"role": m["role"], "content": m["content"]} for m in messages]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = tok(text, return_tensors="pt").to(model.device)
        do_sample = self.cfg.temperature and self.cfg.temperature > 0
        with torch.no_grad():
            out = model.generate(
                **inputs, max_new_tokens=self.cfg.max_tokens,
                do_sample=bool(do_sample),
                temperature=self.cfg.temperature if do_sample else None,
                pad_token_id=tok.eos_token_id,
            )
        gen = out[0][inputs["input_ids"].shape[1]:]
        return tok.decode(gen, skip_special_tokens=True)

    @staticmethod
    def _redirect_hf_cache():
        import os
        import tempfile

        if os.environ.get("HF_HOME"):
            return
        probe = os.path.expanduser("~/.cache/huggingface/hub/__wtest")
        try:
            os.makedirs(probe, exist_ok=True)
            os.rmdir(probe)
        except Exception:
            base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
            os.environ["HF_HOME"] = os.path.join(base, "Jarvis", "hf")


def _dtype_kwarg(dtype) -> dict:
    """transformers >=5 uses `dtype`; older uses `torch_dtype`."""
    import inspect

    from transformers import AutoModelForCausalLM  # type: ignore
    try:
        params = inspect.signature(AutoModelForCausalLM.from_pretrained).parameters
        return {"dtype": dtype} if "dtype" in params else {"torch_dtype": dtype}
    except Exception:
        return {"torch_dtype": dtype}


# --------------------------------------------------------------------------- #
# OpenAI-compatible (llama.cpp server, LM Studio, vLLM, OpenRouter, ...)
# --------------------------------------------------------------------------- #

class OpenAICompatBrain(Brain):
    def complete(self, system, messages, image=None) -> str:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            msgs.append({"role": m["role"], "content": m["content"]})
        imgs = self._as_images(image)
        if imgs and self.cfg.use_vision and msgs:
            last = msgs[-1]
            last["content"] = [{"type": "text", "text": last["content"]}] + [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{self._png_b64(i)}"}}
                for i in imgs
            ]

        headers = {"Content-Type": "application/json"}
        key = getattr(self.cfg, "api_key", "") or os.environ.get(self.cfg.api_key_env, "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": self.cfg.model,
            "messages": msgs,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        base = self.cfg.base_url.rstrip("/")
        url = base + ("/chat/completions" if base.endswith("/v1")
                      else "/v1/chat/completions")
        try:
            r = self._http_post(
                url,
                json=payload,
                headers=headers,
                timeout=self.cfg.request_timeout,
            )
        except Exception as exc:
            raise BrainError(f"cannot reach endpoint {url}: {exc}") from exc
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------- #
# Anthropic (strong vision reference brain)
# --------------------------------------------------------------------------- #

class AnthropicBrain(Brain):
    def complete(self, system, messages, image=None) -> str:
        key = getattr(self.cfg, "api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise BrainError("set ANTHROPIC_API_KEY or api_key in config/env to use the anthropic backend")

        # Anthropic requires strictly alternating roles; our loop emits two
        # consecutive user turns (RESULT then the fresh observation), so merge
        # any consecutive same-role messages first.
        api_msgs = _coalesce_roles(messages)
        imgs = self._as_images(image)
        if imgs and self.cfg.use_vision and api_msgs:
            last = api_msgs[-1]
            last["content"] = [{"type": "text", "text": last["content"]}] + [
                {"type": "image", "source": {"type": "base64",
                 "media_type": "image/png", "data": self._png_b64(i)}}
                for i in imgs
            ]

        payload = {
            "model": self.cfg.model,
            "system": system,
            "messages": api_msgs,
            "max_tokens": self.cfg.max_tokens,
            "temperature": self.cfg.temperature,
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        r = self._http_post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers=headers,
            timeout=self.cfg.request_timeout,
        )
        r.raise_for_status()
        data = r.json()
        parts = [b.get("text", "") for b in data.get("content", [])
                 if b.get("type") == "text"]
        return "".join(parts)


# --------------------------------------------------------------------------- #
# Google Cloud Vertex AI (Gemini)
# --------------------------------------------------------------------------- #

class GeminiVertexBrain(Brain):
    """Google Cloud Vertex AI backend using Application Default Credentials (ADC)."""

    def __init__(self, cfg: BrainConfig):
        super().__init__(cfg)
        self.project_id = None
        self._cached_token = None
        self._token_expiry = 0
        self._auth_lock = threading.Lock()

    def _get_access_token_and_project(self) -> tuple[str, str]:
        if (
            self._cached_token
            and self.project_id
            and time.time() < self._token_expiry - 60
        ):
            return self._cached_token, self.project_id
        with self._auth_lock:
            # An interactive warmup, foreground request, and background TTS
            # can arrive together. Only one of them should refresh ADC.
            if (
                self._cached_token
                and self.project_id
                and time.time() < self._token_expiry - 60
            ):
                return self._cached_token, self.project_id
            return self._refresh_access_token_and_project()

    def warmup(self) -> None:
        self._get_access_token_and_project()

    def _refresh_access_token_and_project(self) -> tuple[str, str]:
        # Never let a failed background warmup leave a fresh token paired with
        # a missing/stale project for the first real foreground request.
        self._cached_token = None
        self.project_id = None
        self._token_expiry = 0

        # Fast-path: Directly parse and refresh from standard ADC JSON
        # (Avoids slow gcloud CLI subshell spawning in google.auth on Windows)
        adc_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        if not adc_path:
            adc_path = os.path.expandvars(r'%APPDATA%\gcloud\application_default_credentials.json')

        if os.path.exists(adc_path):
            try:
                with open(adc_path, 'r', encoding='utf-8') as f:
                    creds = json.load(f)

                cred_type = creds.get('type')
                project_id = creds.get('quota_project_id') or creds.get('project_id') or os.environ.get('GOOGLE_CLOUD_PROJECT')

                if cred_type == 'authorized_user' and creds.get('client_id') and creds.get('refresh_token') and project_id:
                    token_url = "https://oauth2.googleapis.com/token"
                    data = urllib.parse.urlencode({
                        'client_id': creds['client_id'],
                        'client_secret': creds.get('client_secret', ''),
                        'refresh_token': creds['refresh_token'],
                        'grant_type': 'refresh_token'
                    }).encode('utf-8')

                    req = urllib.request.Request(
                        token_url,
                        data=data,
                        headers={'Content-Type': 'application/x-www-form-urlencoded'}
                    )
                    with urllib.request.urlopen(
                        req,
                        timeout=min(10.0, float(self.cfg.request_timeout or 10.0)),
                    ) as response:
                        res = json.loads(response.read().decode('utf-8'))
                        self._cached_token = res['access_token']
                        self.project_id = project_id
                        self._token_expiry = time.time() + res.get('expires_in', 3600)
                        return self._cached_token, self.project_id
            except Exception as direct_exc:
                log.debug(f"Direct ADC refresh fast-path bypassed: {direct_exc}")

        # Fallback: Try using google-auth library if installed (for service accounts / metadata server)
        ga_error = ""
        try:
            import google.auth  # type: ignore
            import google.auth.transport.requests  # type: ignore
            credentials, project = google.auth.default()
            request = google.auth.transport.requests.Request()
            credentials.refresh(request)
            token = credentials.token
            project_id = (
                project
                or getattr(credentials, 'project_id', None)
                or os.environ.get('GOOGLE_CLOUD_PROJECT')
            )
            if token and project_id:
                self._cached_token = token
                self.project_id = project_id
                self._token_expiry = time.time() + 3500
                return self._cached_token, self.project_id
        except ImportError:
            pass
        except Exception as exc:
            ga_error = str(exc)

        detail = f" (google-auth: {ga_error})" if ga_error else ""
        raise BrainError(
            f"Could not authenticate Google Cloud credentials for Gemini Vertex AI{detail}. "
            f"Please run 'gcloud auth application-default login' or switch to a local backend in config.yaml."
        )

    def complete(self, system: str, messages: list[dict], image=None) -> str:
        access_token, project_id = self._get_access_token_and_project()

        contents = []
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            parts = []
            if isinstance(m["content"], str):
                parts.append({"text": m["content"]})
            elif isinstance(m["content"], list):
                for part in m["content"]:
                    if part.get("type") == "text":
                        parts.append({"text": part["text"]})
                    elif part.get("type") == "image_url":
                        pass
            contents.append({"role": role, "parts": parts})

        imgs = self._as_images(image)
        if imgs and self.cfg.use_vision and contents:
            for msg in reversed(contents):
                if msg["role"] == "user":
                    for im in imgs:
                        b64, mime = self._prepare_vision_b64(im)
                        msg["parts"].append({
                            "inlineData": {
                                "mimeType": mime,
                                "data": b64
                            }
                        })
                    break
            # Gemini's native pointing convention is 0-1000 normalized; pin it
            # down so raw coordinates are unambiguous (denormalized in
            # tools/registry.py). Element ids are still preferred and exact.
            system = (system or "") + (
                "\n\nVISION COORDINATES: prefer clicking by element id from the "
                "list. If you must give raw x/y coordinates, normalize them to "
                "a 0-1000 scale relative to the screenshot: (0,0) is the "
                "top-left corner, (1000,1000) the bottom-right. Never copy "
                "pixel coordinates from the element list into x/y - use the "
                "element id instead.")

        max_tokens = self.cfg.max_tokens
        if max_tokens is None or max_tokens <= 0 or max_tokens > 50000:
            max_tokens = 50000

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.cfg.temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json"
            }
        }

        if system:
            payload["systemInstruction"] = {
                "parts": [{"text": system}]
            }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        loc = getattr(self.cfg, "location", "global")
        url = f"https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/{loc}/publishers/google/models/{self.cfg.model}:generateContent"

        # Gemini sometimes returns an EMPTY candidate with finishReason
        # RECITATION (its copyright filter matching benign output), OTHER, or
        # MALFORMED_FUNCTION_CALL. The latter is common when an agent prompt
        # describes actions in function-like notation: Gemini tries to emit a
        # native call even though this backend asked for a JSON text envelope.
        #
        # A blind retry is ineffective for malformed calls (and increasing the
        # temperature makes them less reliable). On that finish reason, retry
        # deterministically with the prompt's actions declared as native
        # functions, then translate the validated functionCall back into the
        # JSON envelope consumed by the rest of Jarvis.
        action_declarations: list[dict] = []
        if "Available actions:" in (system or ""):
            from ..tools.schema import to_json_schema

            schemas = to_json_schema()
            mentioned = {
                schema["name"] for schema in schemas
                if f"\n  {schema['name']}(" in f"\n{system}"
            }
            action_declarations = [
                schema for schema in schemas if schema["name"] in mentioned
            ]

        last_err = ""
        recover_malformed_call = False
        for attempt in range(3):
            request_payload = payload
            if recover_malformed_call:
                # Copy only for the recovery request so the ordinary request
                # remains byte-for-byte compatible with existing models.
                import copy

                request_payload = copy.deepcopy(payload)
                request_payload["generationConfig"]["temperature"] = 0.0
                # Vertex rejects forced function calling (ANY) when structured
                # JSON output is enabled. The response arrives as a native
                # functionCall part, so text/plain is the compatible transport
                # for this recovery request.
                request_payload["generationConfig"]["responseMimeType"] = (
                    "text/plain"
                )
                recovery_note = (
                    "Your previous response was rejected as a malformed "
                    "function call. Produce exactly one valid action now. "
                    "Ensure every string argument is properly JSON-escaped."
                )
                request_payload.setdefault(
                    "systemInstruction", {"parts": []}
                )["parts"].append({"text": recovery_note})
                if action_declarations:
                    request_payload["tools"] = [{
                        "functionDeclarations": action_declarations
                    }]
                    request_payload["toolConfig"] = {
                        "functionCallingConfig": {"mode": "ANY"}
                    }
            elif attempt:
                payload["generationConfig"]["temperature"] = min(
                    1.0, max(self.cfg.temperature, 0.2) + 0.35 * attempt)

            try:
                r = self._http_post(
                    url,
                    json=request_payload,
                    headers=headers,
                    timeout=self.cfg.request_timeout,
                )
            except Exception as exc:
                raise BrainError(
                    f"Vertex AI Gemini API request failed: {exc}") from exc
            if not r.ok:
                try:
                    err_details = f" - Details: {r.text[:500]}"
                except Exception:
                    err_details = ""
                raise BrainError(
                    f"Vertex AI Gemini API returned HTTP {r.status_code}{err_details}")
            data = r.json()

            candidates = data.get("candidates", [])
            if candidates:
                candidate = candidates[0]
                parts = candidate.get("content", {}).get("parts", [])
                # Join every text part; skip "thought" parts (2.5+ models).
                text = "".join(p.get("text", "") for p in parts
                               if isinstance(p, dict) and not p.get("thought"))
                if text.strip():
                    return text
                # Forced function calling is used only as recovery transport.
                # Convert its structured result back to Jarvis's stable JSON
                # action contract so no caller needs Gemini-specific logic.
                for part in parts:
                    call = (part.get("functionCall")
                            if isinstance(part, dict) else None)
                    if not isinstance(call, dict) or not call.get("name"):
                        continue
                    args = call.get("args", {})
                    if not isinstance(args, dict):
                        args = {}
                    return json.dumps({
                        "thought": "",
                        "action": str(call["name"]),
                        "args": args,
                    }, ensure_ascii=False)
                finish = candidate.get("finishReason", "UNKNOWN")
                detail = str(candidate.get("finishMessage", "")).strip()
            else:
                finish = "NO_CANDIDATES (response might have been blocked)"
                detail = ""
            last_err = f"Empty content from Gemini. Finish reason: {finish}"
            if detail:
                last_err += f". {detail[:300]}"
            recover_malformed_call = finish == "MALFORMED_FUNCTION_CALL"

        raise BrainError(last_err + " (after 3 attempts)")

    def transcribe_audio(self, wav_bytes: bytes, *,
                         model: str | None = None) -> str:
        """Speech-to-text: Gemini accepts audio natively, so voice input needs
        no local speech model on this machine."""
        access_token, project_id = self._get_access_token_and_project()
        transcription_model = model or self.cfg.model
        b64 = base64.b64encode(wav_bytes).decode("ascii")
        if transcription_model.startswith("gemini-2.5"):
            thinking_config = {"thinkingBudget": 0}
        else:
            thinking_config = {"thinkingLevel": "minimal"}
        payload = {
            "contents": [{"role": "user", "parts": [
                {"text": "Transcribe this audio exactly as spoken. Reply with "
                         "ONLY the transcribed text - no quotes, no commentary. "
                         "If there is no intelligible speech, reply with an "
                         "empty string."},
                {"inlineData": {"mimeType": "audio/wav", "data": b64}},
            ]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 128,
                "thinkingConfig": thinking_config,
            },
        }
        headers = {"Authorization": f"Bearer {access_token}",
                   "Content-Type": "application/json"}
        loc = getattr(self.cfg, "location", "global")
        url = (f"https://aiplatform.googleapis.com/v1/projects/{project_id}"
               f"/locations/{loc}/publishers/google/models/"
               f"{transcription_model}:generateContent")
        r = self._http_post(
            url,
            json=payload,
            headers=headers,
            timeout=self.cfg.request_timeout,
        )
        if not r.ok:
            raise BrainError(f"transcription HTTP {r.status_code}: {r.text[:200]}")
        candidates = r.json().get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts
                       if isinstance(p, dict) and not p.get("thought")).strip()

    def _parse_tts_response(self, response: Any) -> bytes:
        candidates = response.json().get("candidates", [])
        parts = (
            candidates[0].get("content", {}).get("parts", [])
            if candidates else []
        )
        audio_chunks = []
        for part in parts:
            inline_data = (
                part.get("inlineData") or part.get("inline_data") or {}
                if isinstance(part, dict) else {}
            )
            encoded = inline_data.get("data")
            if encoded:
                audio_chunks.append(base64.b64decode(encoded))
        if not audio_chunks:
            raise BrainError("Gemini TTS returned no audio data")
        return b"".join(audio_chunks)

    def synthesize_speech(self, text: str, *, model: str, voice_name: str,
                          language_code: str = "en-US") -> bytes:
        """Generate 24 kHz, mono, signed 16-bit PCM with a Gemini TTS model.

        ``model`` is supplied explicitly so speech generation can never
        accidentally replace or mutate ``self.cfg.model``, the thinking model.
        """
        api_key = getattr(self.cfg, "api_key", None) or os.environ.get("GEMINI_API_KEY")
        speech_config: dict[str, Any] = {
            "voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": voice_name}
            }
        }
        if language_code:
            speech_config["languageCode"] = language_code
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{
                    "text": (
                        "Read the following exactly as written in a warm, "
                        f"clear, natural assistant voice:\n{text}"
                    )
                }],
            }],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": speech_config,
            },
        }

        # Try Google AI Studio if API key is present
        if api_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            try:
                response = self._http_post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.cfg.request_timeout,
                )
                if response.ok:
                    return self._parse_tts_response(response)
            except Exception:
                pass

        # Try Vertex AI via Application Default Credentials
        access_token, project_id = self._get_access_token_and_project()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        loc = getattr(self.cfg, "location", "global")
        url = (
            "https://aiplatform.googleapis.com/v1beta1/projects/"
            f"{project_id}/locations/{loc}/publishers/google/models/"
            f"{model}:generateContent"
        )
        try:
            response = self._http_post(
                url,
                json=payload,
                headers=headers,
                timeout=self.cfg.request_timeout,
            )
        except Exception as exc:
            raise BrainError(
                f"Vertex AI Gemini TTS request failed: {exc}"
            ) from exc
        if not response.ok:
            raise BrainError(
                "Vertex AI Gemini TTS returned HTTP "
                f"{response.status_code}: {response.text[:500]}"
            )

        return self._parse_tts_response(response)
