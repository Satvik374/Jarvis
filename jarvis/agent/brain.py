"""The pluggable LLM 'brain'.

A Brain takes the running message history and returns the model's next raw
reply (a JSON action string, per prompts.py). Four backends ship:

  * ``ollama``   - local, default. Talks to the Ollama server (localhost:11434).
  * ``llamacpp`` - local, via a llama.cpp / llama-cpp-python OpenAI server.
  * ``openai``   - any OpenAI-compatible endpoint (LM Studio, vLLM, ...).
  * ``openrouter``- OpenRouter: any hosted model, including the free `:free` ones.
  * ``anthropic``- Claude API, useful as the strong-vision reference brain.

All backends share one interface so the rest of the app never branches on which
model is in use. Vision (sending the annotated screenshot) is supported by the
ollama and openai/anthropic backends when ``use_vision`` is on.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import json
import os
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, NamedTuple

from ..config import BrainConfig
from ..providers import (apply_defaults, attribution_headers, busy_status,
                         default_base_url, env_api_key, image_refusal, is_busy_error,
                         is_transient_error, provider_error, provider_for,
                         same_provider)
from ..utils import logging as log
from ..utils.adc import adc_path as find_adc_path


class BrainError(RuntimeError):
    """A provider or transport failure. ``status`` is the HTTP code when there was one.

    The retry policy reads that number, not the wording: a busy 504 once counted
    as retryable only because its text happened to say "timeout".
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ProviderBusy(BrainError):
    """Out of capacity: a busy status, or a 200 carrying no text.

    ``silent`` says which, and that shape has no status to read.
    """

    def __init__(self, message: str, status: int | None = None, silent: bool = False):
        super().__init__(message, status)
        self.silent = silent


class VisionState(NamedTuple):
    """What the setting says, what can really be used now, why not, and until when."""

    configured: bool
    usable: bool
    reason: str = ""
    retry_in: int = 0


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

    def vision_state(self) -> VisionState:
        """Screenshots are usable exactly when the setting asks (brains that throttle override)."""
        return VisionState(self.cfg.use_vision, self.cfg.use_vision)

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
    def _vision_b64(image, quality: int = 90) -> tuple[str, str]:
        """JPEG-encode a screenshot for a vision call, at the size it was taken.

        Returns ``(base64_string, mime_type)``. The same 1920x1080 frame costs
        ~80ms to encode and ~950KiB to upload as PNG, on every step of every
        task; as JPEG it is ~6ms and ~330KiB. The dimensions are deliberately
        untouched: a backend that answers in image pixels would have every
        click silently rescaled by a resize. Callers that do want a reshaped
        image (Codex, Foundry) use ``_prepare_vision_b64``.
        """
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"

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


# Long honest waits for a capacity window (~95s in total), then a clear stop;
# which failures count as busy is the provider module's answer.
_BUSY_BUDGET = 5
_BUSY_DELAYS = (5, 15, 30, 45)

def is_provider_busy(exc: BaseException) -> bool:
    """The ONE verdict on "out of capacity": the status, the wording, or our marker for
    a route that answered 200 with nothing. ``logging`` reads it too, so a busy
    failure cannot arrive as a generic "something went wrong".
    """
    return (isinstance(exc, ProviderBusy)          # a silent route has no status
            or busy_status(getattr(exc, "status", None))
            or is_busy_error(exc))


def complete_with_retry(brain: "Brain", system: str, messages: list[dict],
                        image=None, tries: int = 3,
                        task_patience: bool = False) -> str:
    """Retry a transient failure instead of killing the task.

    A caller on a task's critical path may wait out a capacity window
    (``task_patience``, ~95s, dropping the screenshot on retry); callers a person is
    waiting on fail in seconds and keep the picture, which there IS the question.
    Other transient errors keep the old backoff.
    """
    attempt, waited = 0, 0.0
    while True:
        try:
            text = brain.complete(system, messages, image=image)
            if not str(text or "").strip():
                # A blank 200 is capacity, not a malformed reply: same policy.
                raise ProviderBusy("the AI service answered with nothing at all",
                                   silent=True)
            return text
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            busy = is_provider_busy(exc)
            transient = busy or is_transient_error(exc)
            budget = (max(_BUSY_BUDGET, tries) if busy and task_patience
                      else max(tries, 5) if transient and not busy else tries)
            attempt += 1
            if attempt >= budget:
                if not busy:
                    raise
                # ProviderBusy, not BrainError: logging keys the sentence off it.
                raise ProviderBusy(
                    f"{exc} - the provider stayed busy through {attempt} "
                    f"attempts, about {waited:.0f}s of waiting. Ask again in "
                    f"a minute, or point the brain at a model with more "
                    f"capacity",
                    status=getattr(exc, "status", None),
                    silent=getattr(exc, "silent", False)) from exc
            delay = (_BUSY_DELAYS[min(attempt - 1, len(_BUSY_DELAYS) - 1)] if busy and task_patience
                     else min(60, 5 * 2 ** (attempt - 1)) if transient else 2 * attempt)
            waited += delay
            dropped = ""
            if busy and task_patience and image is not None:
                # Stop paying for the failing route; the element list still grounds it.
                image, dropped = None, (" - the screenshot route is the one failing, "
                                        "so the next attempt goes without it")
            log.warn(f"brain hiccup (attempt {attempt}/{budget}): {exc} "
                     f"- retrying in {delay:.0f}s{dropped}")
            time.sleep(delay)


def _coalesce_roles(messages: list[dict]) -> list[dict]:
    """Merge consecutive same-role messages into one (alternation required by providers like Anthropic and Gemini)."""
    out: list[dict] = []
    for m in messages:
        if not out or out[-1]["role"] != m["role"]:
            content = m.get("content", "")
            if isinstance(content, list):
                content = list(content)
            out.append({"role": m["role"], "content": content})
            continue

        prev = out[-1]["content"]
        curr = m.get("content", "")

        if isinstance(prev, str) and isinstance(curr, str):
            out[-1]["content"] = prev + "\n\n" + curr
        elif isinstance(prev, list) and isinstance(curr, list):
            out[-1]["content"] = prev + curr
        elif isinstance(prev, list) and isinstance(curr, str):
            out[-1]["content"] = prev + [{"type": "text", "text": curr}]
        elif isinstance(prev, str) and isinstance(curr, list):
            out[-1]["content"] = [{"type": "text", "text": prev}] + curr
        else:
            out[-1]["content"] = str(prev) + "\n\n" + str(curr)

    return out


def make_brain(cfg: BrainConfig) -> Brain:
    """Build the brain a backend name selects; which names exist is the table's answer."""
    provider = provider_for(cfg.backend)
    if provider is None:
        raise BrainError(f"unknown brain backend: {cfg.backend}")
    if provider.kind == "openai":
        # OmniRoute first: it mints its own keys, so the sk-or- heuristic must
        # not send a gateway key to openrouter.ai.
        if not same_provider(cfg.backend, "omniroute") and (
                same_provider(cfg.backend, "openrouter")
                or (cfg.api_key and cfg.api_key.startswith("sk-or-"))):
            provider = provider_for("openrouter")
        apply_defaults(cfg, provider)
        return OpenAICompatBrain(cfg)
    transports = {"ollama": OllamaBrain, "hf": HFLocalBrain, "anthropic": AnthropicBrain,
                  "gemini": GeminiVertexBrain, "foundry": AzureFoundryBrain, "codex": OpenAICodexBrain}
    transport = transports.get(provider.kind)
    if transport is None:
        raise BrainError(f"unknown brain kind {provider.kind!r} for backend {cfg.backend!r}")
    return transport(cfg)


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
            msgs[-1]["images"] = [self._vision_b64(i)[0] for i in imgs]

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
# Stop attaching screenshots after a capacity refusal: the gateway refuses the
# whole request and the limit clears on its own, so this is a pause, not a switch.
    _VISION_PAUSE_SECONDS = 120.0

    def __init__(self, cfg: BrainConfig):
        super().__init__(cfg)
        self._vision_paused_until, self._vision_pause_reason = 0.0, ""

    def vision_state(self) -> VisionState:
        """The live answer: the setting, less any active pause."""
        if not self.cfg.use_vision:
            return VisionState(False, False)
        waiting = self._vision_paused_until - time.time()
        return (VisionState(True, False, self._vision_pause_reason, int(waiting) + 1)
                if waiting > 0 else VisionState(True, True))

    def _post_chat(self, url: str, payload: dict, headers: dict):
        """One request, with the transport failure translated."""
        try:
            return self._http_post(url, json=payload, headers=headers,
                                   timeout=self.cfg.request_timeout)
        except Exception as exc:
            raise BrainError(f"cannot reach endpoint {url}: {exc}") from exc

    def _retry_as_text(self, msgs: list[dict], payload: dict, url: str, headers: dict):
        """Resend the turn without screenshots on fresh messages and payload, so
        the caller's dicts and the first body are left untouched."""
        text_only = [{"role": m["role"], "content": "\n".join(
            p.get("text", "") for p in m["content"] if p.get("type") == "text")}
            if isinstance(m.get("content"), list) else m for m in msgs]
        return self._post_chat(url, {**payload, "messages": text_only}, headers)

    def complete(self, system, messages, image=None) -> str:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in _coalesce_roles(messages):
            msgs.append({"role": m["role"], "content": m["content"]})
        imgs = self._as_images(image)
        # `imgs` is what the caller offered, `attached` what this request carries.
        attached = bool(imgs and msgs and self.vision_state().usable)
        if attached:
            shots = [self._vision_b64(i) for i in imgs]
            last = msgs[-1]
            last["content"] = [{"type": "text", "text": last["content"]}] + [
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}"}}
                for b64, mime in shots
            ]

        headers = {"Content-Type": "application/json"}
        key = getattr(self.cfg, "api_key", "") or env_api_key(
            getattr(self.cfg, "backend", ""), getattr(self.cfg, "base_url", ""),
            getattr(self.cfg, "api_key_env", ""))  # the table's variable order
        if not key:
            try:
                from ..security import get_secret
                key = get_secret("OPENROUTER_API_KEY") or get_secret("OPENAI_API_KEY") or get_secret("JARVIS_API_KEY") or ""
            except Exception:
                pass
        if key:
            headers["Authorization"] = f"Bearer {key}"
        headers.update(attribution_headers(getattr(self.cfg, "backend", ""),
                                           self.cfg.base_url, key))

        payload = {
            "model": self.cfg.model,
            "messages": msgs,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        # Nothing configured: the table's own default for that provider.
        base = (self.cfg.base_url.rstrip("/") if self.cfg.base_url
                else default_base_url(getattr(self.cfg, "backend", ""), key))
        url = base + ("/chat/completions" if base.endswith("/v1")
                      else "/v1/chat/completions")
        r = self._post_chat(url, payload, headers)

        # Both refusals share one remedy - resend without screenshots - but the first is permanent.
        if not getattr(r, "ok", True) and attached:
            refusal = image_refusal(r)
            if refusal == "rejected":
                # Say so: silence would leave a :status line reading "Vision off" as if chosen.
                log.warn(f"'{self.cfg.model}' does not accept images - "
                         "screenshots are off for the rest of this run.")
                self.cfg.use_vision = False
            elif refusal == "congested":
                self._vision_paused_until = time.time() + self._VISION_PAUSE_SECONDS
                self._vision_pause_reason = "the service was too busy to accept images"
            if refusal:
                r = self._retry_as_text(msgs, payload, url, headers)

        if not getattr(r, "ok", True):
            raise BrainError(provider_error(url, r, self.cfg.model),
                             status=getattr(r, "status_code", None))
        data = r.json()
        choice = data["choices"][0]
        content = choice["message"].get("content")
        if content is None:
            content = choice["message"].get("reasoning", "") or ""
        return content


# --------------------------------------------------------------------------- #
# OpenAI Codex CLI ("Sign in with ChatGPT" OAuth)
# --------------------------------------------------------------------------- #

class OpenAICodexBrain(Brain):
    """Brain backed by OpenAI Codex CLI "Sign in with ChatGPT" OAuth session.

    Connects to https://chatgpt.com/backend-api/codex with Authorization Bearer
    and ChatGPT-Account-ID headers, parsing Server-Sent Events (SSE) responses.
    """

    def __init__(self, cfg: BrainConfig):
        super().__init__(cfg)
        from ..auth import codex_oauth
        self.auth = codex_oauth
        if not self.cfg.base_url:
            self.cfg.base_url = provider_for("codex").base_url
        if not self.cfg.model:
            self.cfg.model = "gpt-5.3-codex"

    def complete(self, system: str, messages: list[dict], image=None) -> str:
        from ..utils import logging as log

        access_token, account_id = self.auth.get_valid_token()

        url = self.cfg.base_url.rstrip("/")
        if not url.endswith("/responses"):
            url = f"{url}/responses"

        input_items: list[dict] = []
        for m in _coalesce_roles(messages):
            role = m["role"]
            content = m.get("content", "")
            part_type = "output_text" if role == "assistant" else "input_text"
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") in ("text", "input_text", "output_text"):
                        parts.append({"type": part_type, "text": item.get("text", "")})
                    elif isinstance(item, str):
                        parts.append({"type": part_type, "text": item})
                input_items.append({"role": role, "content": parts or [{"type": part_type, "text": ""}]})
            else:
                input_items.append({"role": role, "content": [{"type": part_type, "text": str(content)}]})

        imgs = self._as_images(image)
        has_images = False
        if imgs and self.cfg.use_vision and input_items:
            last = input_items[-1]
            last_content = last.setdefault("content", [])
            for img in imgs:
                try:
                    b64, mime = self._prepare_vision_b64(img)
                    last_content.append({
                        "type": "input_image",
                        "image_url": f"data:{mime};base64,{b64}",
                    })
                    has_images = True
                except Exception as exc:
                    log.warning(f"Could not encode screenshot for vision: {exc}")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "ChatGPT-Account-ID": account_id,
            "Content-Type": "application/json",
            "originator": "codex_cli_rs",
            "User-Agent": "OpenAI-Codex-CLI/0.153.4",
            "Accept": "text/event-stream",
        }

        # Candidate models: try configured model first; if rejected by tier, fallback
        available_models: list[str] = []
        cache_file = Path.home() / ".codex" / "models_cache.json"
        if cache_file.exists():
            try:
                cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
                for m_info in cache_data.get("models", []):
                    slug = m_info.get("slug")
                    if slug and slug not in available_models and slug != "codex-auto-review":
                        available_models.append(slug)
            except Exception:
                pass

        if not available_models:
            available_models = ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-reserve", "gpt-5.4-mini"]

        models_to_try = [self.cfg.model]
        for m in available_models:
            if m not in models_to_try:
                models_to_try.append(m)

        last_error = None
        for model_idx, model_name in enumerate(models_to_try):
            payload = {
                "model": model_name,
                "instructions": system,
                "input": input_items,
                "stream": True,
                "store": False,
            }

            try:
                r = self._http_post(
                    url,
                    json=payload,
                    headers=headers,
                    stream=True,
                    timeout=self.cfg.request_timeout or 60,
                )
            except Exception as exc:
                raise BrainError(f"Cannot reach Codex backend at {url}: {exc}") from exc

            # Proactive token refresh on 401
            if r.status_code == 401:
                log.warn("Codex backend returned 401. Refreshing token...")
                access_token, account_id = self.auth.get_valid_token(force_refresh=True)
                headers["Authorization"] = f"Bearer {access_token}"
                headers["ChatGPT-Account-ID"] = account_id
                r = self._http_post(
                    url,
                    json=payload,
                    headers=headers,
                    stream=True,
                    timeout=self.cfg.request_timeout or 60,
                )

            # Vision fallback if backend rejected images
            if not r.ok and has_images:
                err_text = r.text
                if "image" in err_text.lower() or "not support" in err_text.lower():
                    has_images = False
                    for item in input_items:
                        item["content"] = [c for c in item.get("content", []) if c.get("type") == "input_text"]
                    payload["input"] = input_items
                    r = self._http_post(
                        url,
                        json=payload,
                        headers=headers,
                        stream=True,
                        timeout=self.cfg.request_timeout or 60,
                    )

            if r.status_code == 400:
                err_body = r.text
                if "not supported when using Codex with a ChatGPT account" in err_body or "does not exist or you do not have access" in err_body:
                    if model_idx == 0 and len(models_to_try) > 1:
                        next_model = models_to_try[1]
                        log.warn(f"Model '{model_name}' is not currently active on this ChatGPT account tier. Falling back to active tier model '{next_model}'.")
                    last_error = err_body
                    continue

            if not r.ok:
                raise BrainError(f"Codex backend error ({r.status_code}): {r.text}")

            output_pieces: list[str] = []
            for line in r.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8", errors="replace")
                if line_str.startswith("data: "):
                    data_str = line_str[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except Exception:
                        continue

                    chunk_type = chunk.get("type")
                    if chunk_type in ("response.output_text.delta", "response.text.delta"):
                        output_pieces.append(chunk.get("delta", ""))
                    elif chunk_type == "response.completed" and not output_pieces:
                        resp_obj = chunk.get("response", {})
                        for out in resp_obj.get("output", []):
                            for c in out.get("content", []):
                                if c.get("type") in ("output_text", "text") and c.get("text"):
                                    output_pieces.append(c["text"])
                    elif "choices" in chunk:
                        for ch in chunk.get("choices", []):
                            delta_content = ch.get("delta", {}).get("content")
                            if delta_content:
                                output_pieces.append(delta_content)

            result_text = "".join(output_pieces).strip()
            if result_text:
                return result_text

            if not output_pieces:
                try:
                    data = r.json()
                    resp_obj = data.get("response", {})
                    for out in resp_obj.get("output", []):
                        for c in out.get("content", []):
                            if c.get("text"):
                                output_pieces.append(c["text"])
                    if not output_pieces and "choices" in data:
                        output_pieces.append(data["choices"][0].get("message", {}).get("content", ""))
                    if output_pieces:
                        return "".join(output_pieces).strip()
                except Exception:
                    pass

        raise BrainError(f"Codex backend call failed for all models: {last_error}")


# --------------------------------------------------------------------------- #
# Microsoft Azure AI Foundry Agent (GPT-6 Astra)
# --------------------------------------------------------------------------- #

class AzureFoundryBrain(Brain):
    """Brain backed by Microsoft Azure AI Foundry Agent Code Template with GPT-6.

    Uses AIProjectClient and openai_client.responses.create with extra_body:
    {"agent_reference": {"name": my_agent, "version": my_version, "type": "agent_reference"}}
    """

    def __init__(self, cfg: BrainConfig):
        super().__init__(cfg)
        self.endpoint = (
            getattr(cfg, "foundry_endpoint", None)
            or cfg.base_url
            or os.environ.get("AZURE_FOUNDRY_ENDPOINT")
            or os.environ.get("JARVIS_FOUNDRY_ENDPOINT")
            or os.environ.get("AZURE_AI_ENDPOINT")
            or "https://satviksingh-resource.services.ai.azure.com/api/projects/satviksingh"
        )
        self.agent_name = (
            getattr(cfg, "foundry_agent_name", None)
            or cfg.model
            or os.environ.get("AZURE_AGENT_NAME")
            or os.environ.get("JARVIS_AGENT_NAME")
            or "gpt-6"
        )
        self.agent_version = (
            getattr(cfg, "foundry_agent_version", None)
            or os.environ.get("AZURE_AGENT_VERSION")
            or os.environ.get("JARVIS_AGENT_VERSION")
            or "1"
        )
        self.tenant_id = (
            getattr(cfg, "azure_tenant_id", None)
            or os.environ.get("AZURE_TENANT_ID")
            or None
        )
        self._project_client = None
        self._openai_client = None
        self._is_local_relay = False
        self._init_lock = threading.Lock()

    def _get_client(self):
        if self._openai_client is not None:
            return self._openai_client
        with self._init_lock:
            if self._openai_client is not None:
                return self._openai_client

            endpoint = (self.endpoint or "").strip()
            try:
                # Validate before selecting or acquiring credentials; urlsplit alone
                # accepts userinfo and silently removes some control characters.
                if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in endpoint) or "\\" in endpoint:
                    raise ValueError
                parsed = urllib.parse.urlsplit(endpoint)
                if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None
                        or parsed.query or parsed.fragment):
                    raise ValueError
                parsed.port  # Reject malformed or out-of-range ports.
            except ValueError:
                raise BrainError("Invalid Foundry endpoint: expected an HTTP(S) URL without userinfo, query, or fragment") from None

            host = parsed.hostname.lower().rstrip(".")
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = host == "localhost"
            # Preserve explicitly configured remote HTTP relays as well as loopback.
            if is_loopback or parsed.scheme == "http":
                from openai import OpenAI
                api_key = (
                    os.environ.get("AZURE_API_KEY")
                    or os.environ.get("FOUNDRY_API_KEY")
                    or os.environ.get("JARVIS_API_KEY")
                    or os.environ.get("OPENAI_API_KEY")
                    or self.cfg.api_key
                    or "SATVIKNOOB"
                )
                self._openai_client = OpenAI(
                    base_url=endpoint.rstrip("/"),
                    api_key=api_key,
                )
                self._is_local_relay = True
                return self._openai_client

            try:
                from azure.ai.projects import AIProjectClient
                from ..auth.azure_auth import get_azure_credential
            except ImportError as exc:
                raise BrainError(
                    "Azure AI Projects SDK is required for the foundry backend.\n"
                    "Please run: pip install azure-ai-projects>=2.1.0 azure-identity>=1.16.0"
                ) from exc

            credential = get_azure_credential(tenant_id=self.tenant_id)
            self._project_client = AIProjectClient(
                endpoint=endpoint,
                credential=credential,
            )

            client_kwargs = {}
            # Only use api_key if it is an explicit Azure key (ignore OpenRouter sk-or-, Gemini AIza, OpenAI sk-proj-)
            azure_key = (
                os.environ.get("AZURE_API_KEY")
                or os.environ.get("AZURE_AI_KEY")
                or os.environ.get("FOUNDRY_API_KEY")
            )
            if not azure_key and self.cfg.api_key:
                k = self.cfg.api_key.strip()
                if not (k.startswith("sk-or-") or k.startswith("AIza") or k.startswith("sk-proj-")):
                    azure_key = k

            if azure_key:
                client_kwargs["api_key"] = azure_key

            self._openai_client = self._project_client.get_openai_client(**client_kwargs)
            return self._openai_client

    def warmup(self) -> None:
        try:
            self._get_client()
        except Exception:
            pass

    def complete(self, system: str, messages: list[dict], image=None) -> str:
        from ..utils import logging as log

        client = self._get_client()

        # Build input messages
        # Azure Foundry Agent Responses API accepts strings or content part lists.
        # For plain text turns: pass content as plain str (compatible with both user and assistant).
        # When structured parts are needed (e.g. for vision):
        # - user parts use {"type": "input_text", "text": ...} / {"type": "input_image", ...}
        # - assistant parts use {"type": "output_text", "text": ...} or plain str
        input_items: list[dict] = []
        for m in _coalesce_roles(messages):
            role = m["role"]
            content = m.get("content", "")
            if isinstance(content, list):
                parts_text = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("text"):
                            parts_text.append(str(item["text"]))
                    elif isinstance(item, str):
                        parts_text.append(item)
                input_items.append({"role": role, "content": "".join(parts_text)})
            else:
                input_items.append({"role": role, "content": str(content)})

        # Inject system prompt into input context if provided.
        # Note: Azure Foundry rejects top-level 'instructions' and 'temperature'
        # when an agent_reference is specified because instructions and parameters
        # are governed by the agent definition in Azure AI Foundry. Providing system
        # instructions as initial context ensures Jarvis system instructions and action
        # schemas are delivered without violating the Azure API schema.
        if system:
            first_user = None
            for item in input_items:
                if item.get("role") == "user":
                    first_user = item
                    break
            if first_user is not None:
                first_content = first_user.get("content", "")
                if isinstance(first_content, str):
                    first_user["content"] = (
                        f"[System Context & Instructions]\n{system}\n\n[User Request]\n{first_content}"
                    )
                elif isinstance(first_content, list):
                    first_content.insert(
                        0,
                        {"type": "input_text", "text": f"[System Context & Instructions]\n{system}\n\n"},
                    )
            else:
                input_items.insert(
                    0,
                    {
                        "role": "user",
                        "content": f"[System Context & Instructions]\n{system}",
                    },
                )

        # Append screenshot for vision if requested
        imgs = self._as_images(image)
        has_images = False
        if imgs and self.cfg.use_vision and input_items:
            target_user = None
            for item in reversed(input_items):
                if item.get("role") == "user":
                    target_user = item
                    break
            if target_user is not None:
                cur_content = target_user.get("content", "")
                if isinstance(cur_content, str):
                    content_parts = [{"type": "input_text", "text": cur_content}]
                elif isinstance(cur_content, list):
                    content_parts = list(cur_content)
                else:
                    content_parts = [{"type": "input_text", "text": ""}]

                for img in imgs:
                    try:
                        b64, mime = self._prepare_vision_b64(img)
                        content_parts.append({
                            "type": "input_image",
                            "image_url": f"data:{mime};base64,{b64}",
                            "detail": "auto",
                        })
                        has_images = True
                    except Exception as exc:
                        log.warning(f"Could not encode screenshot for vision: {exc}")
                target_user["content"] = content_parts

        def _call_create(items: list[dict]):
            if getattr(self, "_is_local_relay", False):
                try:
                    return client.responses.create(input=items)
                except Exception as resp_err:
                    log.debug(f"responses.create on local relay failed ({resp_err}); trying chat.completions fallback")
                    chat_msgs = []
                    for item in items:
                        content = item.get("content", "")
                        if isinstance(content, list):
                            parts_text = [str(c.get("text", "")) for c in content if isinstance(c, dict) and c.get("text")]
                            content = "".join(parts_text)
                        chat_msgs.append({"role": item.get("role", "user"), "content": content})
                    resp = client.chat.completions.create(
                        model=self.agent_name or "gpt-6",
                        messages=chat_msgs,
                        temperature=self.cfg.temperature,
                        max_tokens=self.cfg.max_tokens,
                    )
                    choice = resp.choices[0]
                    content = choice.message.content or ""
                    class _SimpleResp:
                        def __init__(self, text):
                            self.output_text = text
                    return _SimpleResp(content)

            extra_body = {
                "agent_reference": {
                    "name": self.agent_name,
                    "version": self.agent_version,
                    "type": "agent_reference",
                }
            }
            # Azure Foundry Agent API requires strict payload matching the template:
            # - No top-level 'instructions' (disallowed when agent is specified)
            # - No top-level 'temperature' (disallowed when agent is specified)
            kwargs = {
                "input": items,
                "extra_body": extra_body,
            }
            return client.responses.create(**kwargs)

        try:
            response = _call_create(input_items)
        except Exception as exc:
            err_msg = str(exc).lower()
            # If backend rejected images, fall back to text-only
            if has_images and any(k in err_msg for k in ("image", "unsupported", "detail", "multimodal")):
                log.warn(f"Agent rejected vision payload ({exc}). Falling back to text-only mode.")
                self.cfg.use_vision = False
                for item in input_items:
                    if isinstance(item.get("content"), list):
                        text_only = "".join(
                            c.get("text", "")
                            for c in item["content"]
                            if isinstance(c, dict) and c.get("type") == "input_text"
                        )
                        item["content"] = text_only
                response = _call_create(input_items)
            else:
                raise BrainError(f"Azure Foundry Agent call failed: {exc}") from exc

        # Extract output text
        output_text = getattr(response, "output_text", None)
        if output_text and output_text.strip():
            return output_text.strip()

        # Deep extraction from response.output items
        output_pieces: list[str] = []
        if hasattr(response, "output") and response.output:
            for item in response.output:
                if hasattr(item, "content") and item.content:
                    for part in item.content:
                        if hasattr(part, "text") and part.text:
                            output_pieces.append(part.text)
                        elif isinstance(part, dict) and part.get("text"):
                            output_pieces.append(part["text"])

        result_text = "".join(output_pieces).strip()
        if result_text:
            return result_text

        raise BrainError("Azure Foundry Agent returned empty content")

    def synthesize_speech(
        self,
        text: str,
        *,
        model: str | None = None,
        voice_name: str = "en-US-OnyxTurboMultilingualNeural",
        language_code: str = "en-US",
    ) -> bytes:
        """Synthesize speech using Microsoft Azure Cognitive Services or Foundry Relay Speech SDK."""
        from ..config import VoiceConfig
        from ..utils.voice import _synthesize_azure_speech

        is_local = getattr(self, "_is_local_relay", False)
        vcfg = VoiceConfig(
            engine="foundry" if is_local else "azure",
            azure_speech_voice=voice_name or "en-US-OnyxTurboMultilingualNeural",
            tts_voice=voice_name or "en-US-OnyxTurboMultilingualNeural",
        )
        return _synthesize_azure_speech(text, vcfg)


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
            shots = [self._vision_b64(i) for i in imgs]
            last = api_msgs[-1]
            last["content"] = [{"type": "text", "text": last["content"]}] + [
                {"type": "image", "source": {"type": "base64",
                 "media_type": mime, "data": b64}}
                for b64, mime in shots
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
    """Google Gemini backend supporting Google AI Studio (API key) and Vertex AI (ADC)."""

    def __init__(self, cfg: BrainConfig):
        super().__init__(cfg)
        self.project_id = None
        self._cached_token = None
        self._token_expiry = 0
        self._auth_lock = threading.Lock()

    def _get_api_key(self) -> str:
        # Explicit Google API key takes precedence
        explicit_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if explicit_key and explicit_key.strip():
            return explicit_key.strip()

        # Check config/generic key, but ensure it is not an OpenAI/OpenRouter/Groq key or dummy placeholder
        key = (getattr(self.cfg, "api_key", None) or os.environ.get("JARVIS_API_KEY") or "").strip()
        if key and (key.startswith("AIza") or (not key.startswith("sk-") and not key.startswith("gsk_") and key != "SATVIKNOOB" and len(key) >= 30)):
            return key

        try:
            from ..security import get_secret
            vault_key = (
                get_secret("GEMINI_API_KEY")
                or get_secret("GOOGLE_API_KEY")
            )
            if vault_key:
                return vault_key.strip()
        except Exception:
            pass
        return ""

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

        # Fast-path: read and refresh the standard ADC JSON directly, so a token
        # costs neither a google-auth import nor a gcloud CLI subshell. The
        # search is shared with the live-voice readiness report (see
        # jarvis.utils.adc): it used to be hard-coded to %APPDATA%, which meant
        # this fast path was dead on Linux and macOS while the report promised
        # credentials it could not read.
        adc_file = find_adc_path()

        if adc_file is not None and adc_file.is_file():
            try:
                with open(adc_file, 'r', encoding='utf-8') as f:
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
        api_key = self._get_api_key()
        if not api_key:
            access_token, project_id = self._get_access_token_and_project()

        contents = []
        for m in _coalesce_roles(messages):
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
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
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

        if api_key:
            clean_model = self.cfg.model.removeprefix("models/")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
        else:
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
            # Gemini's functionDeclarations accept only an OpenAPI subset:
            # minimum/maximum would be rejected with 400 INVALID_ARGUMENT for
            # every action at once, so this transport gets the sanitized view
            # (bounds folded into description text instead).
            from ..tools.schema import gemini_safe_json_schema

            schemas = gemini_safe_json_schema()
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
                endpoint_name = "Gemini" if api_key else "Vertex AI Gemini"
                raise BrainError(
                    f"{endpoint_name} API request failed: {exc}") from exc
            if not r.ok:
                try:
                    err_details = f" - Details: {r.text[:500]}"
                except Exception:
                    err_details = ""
                endpoint_name = "Gemini" if api_key else "Vertex AI Gemini"
                raise BrainError(
                    f"{endpoint_name} API returned HTTP {r.status_code}{err_details}")
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
        api_key = self._get_api_key()
        transcription_model = model or self.cfg.model
        clean_model = transcription_model.removeprefix("models/")
        b64 = base64.b64encode(wav_bytes).decode("ascii")
        if clean_model.startswith("gemini-2.5"):
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
        if api_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
        else:
            access_token, project_id = self._get_access_token_and_project()
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
        api_key = self._get_api_key()
        clean_model = model.removeprefix("models/")
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
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
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
                raise BrainError(
                    f"Gemini TTS returned HTTP {response.status_code}: {response.text[:500]}"
                )
            except Exception as exc:
                if isinstance(exc, BrainError):
                    raise
                raise BrainError(f"Gemini TTS request failed: {exc}") from exc

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
