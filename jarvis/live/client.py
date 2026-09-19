"""Gemini Flash Live WebSocket client for real-time multimodal voice streaming."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

import websockets

from ..config import LiveVoiceConfig
from ..utils import logging as log
from . import gemini_live


#: Live model names the Live API no longer serves, mapped to their replacement.
#:
#: Verified against the API's own model listing - `gemini-2.0-flash-exp` is
#: absent from it while `gemini-3.1-flash-live-preview` is present. Kept as a
#: name here because that is what the tests and the terminal HUD refer to; the
#: table itself lives with the rest of the protocol knowledge, so the terminal
#: supervisor and the browser relay cannot disagree about what is retired.
RETIRED_LIVE_MODELS: dict[str, str] = gemini_live.RETIRED_MODELS


class GeminiLiveClient:
    """Real-time bidirectional WebSocket client for Gemini Live API."""

    def __init__(
        self,
        config: LiveVoiceConfig,
        system_instruction: str = "",
        on_audio_out: Callable[[bytes], None] | None = None,
        on_text: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
        on_interrupted: Callable[[], None] | None = None,
    ):
        self.config = config
        self.system_instruction = system_instruction
        self.on_audio_out = on_audio_out
        self.on_text = on_text
        self.on_tool_call = on_tool_call
        self.on_interrupted = on_interrupted

        self._ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._is_running = False
        self._is_connected = False
        self._outbound_queue: asyncio.Queue | None = None
        self._cached_token = ""
        self._token_expiry = 0.0
        self._project_id = ""
        # Google Search grounding is offered on the first attempt only. The API
        # answers a grounding refusal with the same generic quota error as a
        # spent Live allowance, so a retry without the search tool is what tells
        # the two apart (see jarvis.live.gemini_live).
        self._grounding_enabled = gemini_live.grounding_wanted(config)
        self.grounding_blocked = ""

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> bool:
        """Start the live WebSocket client background loop."""
        if self._is_running:
            return True
        backend = (self.config.backend or "api_key").strip().lower()
        if backend == "api_key" and not self._api_key():
            log.error("Gemini Live needs an API key. Set JARVIS_LIVE_API_KEY or GEMINI_API_KEY "
                      "instead of putting it in config.yaml.")
            return False
        self._is_running = True
        started_event = threading.Event()

        def _thread_target():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._outbound_queue = asyncio.Queue()
            self._loop.run_until_complete(self._run_loop(started_event))

        self._thread = threading.Thread(
            target=_thread_target,
            daemon=True,
            name="jarvis-gemini-live-ws",
        )
        self._thread.start()
        started_event.wait(timeout=5.0)
        return self._is_running

    def stop(self) -> None:
        """Disconnect and stop live client."""
        self._is_running = False
        self._is_connected = False
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)

    def send_audio(self, pcm_bytes: bytes) -> None:
        """Send 16kHz PCM audio chunk to the live model."""
        if not self._is_connected or not pcm_bytes or self._loop is None:
            return
        b64 = base64.b64encode(pcm_bytes).decode("ascii")
        msg = {
            "realtimeInput": {
                "audio": {
                    "mimeType": "audio/pcm;rate=16000",
                    "data": b64,
                }
            }
        }
        self._post_message(msg)

    def send_video_frame(self, jpeg_bytes: bytes) -> None:
        """Send one screen frame to the live model.

        Frames must keep arriving for the model to see anything: a single still
        was answered with "I cannot see any image", while frames sent about once
        a second were described correctly. Callers stream, they do not snapshot.
        """
        if not self._is_connected or not jpeg_bytes or self._loop is None:
            return
        msg = {
            "realtimeInput": {
                "video": {
                    "mimeType": "image/jpeg",
                    "data": base64.b64encode(jpeg_bytes).decode("ascii"),
                }
            }
        }
        self._post_message(msg)

    def send_audio_stream_end(self) -> None:
        """Tell the model the microphone stopped, so it can finish its turn.

        Only meaningful while automatic activity detection is on (the default);
        without it a muted microphone looks like a very long silence.
        """
        if not self._is_connected or self._loop is None:
            return
        self._post_message({"realtimeInput": {"audioStreamEnd": True}})

    def send_text_turn(self, text: str) -> None:
        """Inject a text message/context notification into the active live session."""
        if not self._is_connected or not text or self._loop is None:
            return
        # Realtime text is valid throughout a Live session. ``clientContent``
        # is only for seeding history on current Gemini 3.1 Live sessions and
        # stops working after the first model turn.
        msg = {"realtimeInput": {"text": text}}
        self._post_message(msg)

    def send_tool_response(
        self,
        call_id: str,
        output: dict[str, Any],
        name: str = "",
    ) -> None:
        """Send tool execution response back to the live model."""
        if not self._is_connected or self._loop is None:
            return
        response: dict[str, Any] = {"id": call_id, "response": {"result": output}}
        if name:
            response["name"] = name
        msg = {
            "toolResponse": {
                "functionResponses": [response]
            }
        }
        self._post_message(msg)

    def _api_key(self) -> str:
        key = (
            self.config.api_key
            or os.environ.get("JARVIS_LIVE_API_KEY", "")
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        ).strip()
        if key:
            return key
        try:
            from ..security import get_secret

            return (
                get_secret("JARVIS_LIVE_API_KEY")
                or get_secret("GEMINI_API_KEY")
                or get_secret("GOOGLE_API_KEY")
                or ""
            ).strip()
        except Exception:
            return ""

    def _post_message(self, msg: dict[str, Any]) -> None:
        if self._loop is not None and self._outbound_queue is not None:
            try:
                self._loop.call_soon_threadsafe(
                    self._outbound_queue.put_nowait, msg
                )
            except Exception as exc:
                log.debug(f"Failed to queue live message: {exc}")

    def _get_auth_token_and_project(self) -> tuple[str, str]:
        """Authenticate via Google Cloud Application Default Credentials (gcloud ADC)."""
        now = time.time()
        if self._cached_token and now < self._token_expiry and self._project_id:
            return self._cached_token, self._project_id

        # Fast path: check local ADC file
        adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not adc_path:
            adc_path = os.path.expandvars(r"%APPDATA%\gcloud\application_default_credentials.json")

        if os.path.exists(adc_path):
            try:
                with open(adc_path, "r", encoding="utf-8") as f:
                    creds = json.load(f)

                cred_type = creds.get("type")
                project_id = creds.get("quota_project_id") or creds.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT")

                if cred_type == "authorized_user" and creds.get("client_id") and creds.get("refresh_token") and project_id:
                    token_url = "https://oauth2.googleapis.com/token"
                    data = urllib.parse.urlencode({
                        "client_id": creds["client_id"],
                        "client_secret": creds.get("client_secret", ""),
                        "refresh_token": creds["refresh_token"],
                        "grant_type": "refresh_token",
                    }).encode("utf-8")

                    req = urllib.request.Request(
                        token_url,
                        data=data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    with urllib.request.urlopen(req, timeout=8.0) as response:
                        res = json.loads(response.read().decode("utf-8"))
                        self._cached_token = res["access_token"]
                        self._project_id = project_id
                        self._token_expiry = time.time() + res.get("expires_in", 3600) - 60
                        return self._cached_token, self._project_id
            except Exception as direct_exc:
                log.debug(f"Direct ADC token refresh bypassed: {direct_exc}")

        # Fallback via google.auth
        try:
            import google.auth
            import google.auth.transport.requests

            credentials, project = google.auth.default()
            request = google.auth.transport.requests.Request()
            credentials.refresh(request)
            token = credentials.token
            project_id = (
                project
                or getattr(credentials, "project_id", None)
                or os.environ.get("GOOGLE_CLOUD_PROJECT")
            )
            if token and project_id:
                self._cached_token = token
                self._project_id = project_id
                self._token_expiry = time.time() + 3500
                return self._cached_token, self._project_id
        except Exception as ga_exc:
            log.debug(f"google.auth default failed: {ga_exc}")

        raise RuntimeError(
            "Could not authenticate Google Cloud credentials for Gemini Live API. "
            "Please run 'gcloud auth application-default login' in terminal."
        )

    def _build_setup_message(
        self,
        project_id: str = "",
        model_name: str | None = None,
        *,
        google_search: bool | None = None,
    ) -> dict[str, Any]:
        loc = self.config.location or "us-central1"
        raw_model = model_name or self.config.model or gemini_live.DEFAULT_MODEL

        if raw_model.startswith("models/"):
            model_resource = raw_model
        elif raw_model.startswith("projects/") or raw_model.startswith("publishers/"):
            model_resource = raw_model
        elif project_id:
            model_resource = f"projects/{project_id}/locations/{loc}/publishers/google/models/{raw_model}"
        else:
            model_resource = f"models/{raw_model}"

        system_text = (
            "You are Jarvis, the real-time AI conversational voice executive and supervisor. "
            "You speak directly to the user in a natural, polite, engaging, British-accented, executive tone.\n\n"
            "INTERACTION PRINCIPLES:\n"
            "1. CONVERSATIONAL MODE (DEFAULT):\n"
            "   - All user spoken and typed messages come directly to you first.\n"
            "   - When the user is talking to you, chatting, asking questions, asking for advice, discussing topics, "
            "     or joking, converse with them directly and immediately in voice. Do NOT invoke any tools for conversation.\n"
            "2. COMPUTER TASK DELEGATION (TOOL CALL):\n"
            "   - ONLY when the user explicitly requests you to perform an action or computer task "
            "     (such as clicking UI elements, launching applications, writing/editing code, running terminal commands, "
            "      extracting web data, or manipulating files), you MUST call the 'run_jarvis_task' tool with the exact "
            "     natural language task instruction to prompt the text-based Jarvis worker.\n"
            "   - Inform the user briefly in voice that you are setting the text agent to work on it.\n"
            "3. SEEING THE SCREEN:\n"
            "   - You do not receive the screen by default. When you need to know what is on the user's "
            "screen, call 'share_screen'; frames then arrive about once a second. Call "
            "'stop_screen_share' as soon as you are done looking.\n"
            "   - To change what a window shows, hand the job to the text agent with 'run_jarvis_task' - "
            "it clicks and types with full screen awareness.\n"
            "4. MID-TASK SUPERVISION AND PROGRESS NARRATION:\n"
            "   - While the text-based Jarvis agent is executing the task, you will receive real-time progress events.\n"
            "   - Monitor what the text-based model is doing and proactively speak to the user, telling them what has "
            "     been done and what will be done next in the mid-task.\n"
            "   - When the task completes, announce the final result smoothly to the user.\n\n"
            "Keep voice responses natural, crisp, polite, and executive."
        )
        if self.system_instruction:
            system_text = f"{system_text}\n\n{self.system_instruction}"

        declarations = [
            {
                "name": "run_jarvis_task",
                "description": (
                    "Prompt the normal text-based Jarvis AI agent to perform a task on the computer "
                    "(e.g. clicking UI, launching apps, typing, web extraction, bash/python coding, file operations)."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "task": {
                            "type": "STRING",
                            "description": "The exact natural language task instructions for Jarvis to execute.",
                        },
                    },
                    "required": ["task"],
                },
            },
            {
                "name": "cancel_task",
                "description": "Immediately cancel/abort the currently running task when the user asks to stop.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "reason": {
                            "type": "STRING",
                            "description": "Optional reason for cancellation.",
                        },
                    },
                },
            },
            {
                "name": "ask_task_status",
                "description": "Query the current state and step progress of the active task.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                },
            },
            {
                "name": "answer_agent_question",
                "description": "Provide the user's answer or clarification to a mid-task question that the Main Worker Agent asked.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "answer": {
                            "type": "STRING",
                            "description": "The user's answer or clarification to the agent's question.",
                        },
                    },
                    "required": ["answer"],
                },
            },
            # Screen sharing, answered locally by the supervisor: it captures the
            # desktop about once a second and streams the frames through this same
            # session while the model wants to look.
            {
                "name": "share_screen",
                "description": (
                    "Start seeing the user's screen so you can check what an app or dialog is showing. "
                    "Frames then arrive about once a second until you call stop_screen_share."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "reason": {
                            "type": "STRING",
                            "description": "Short reason you need to look.",
                        },
                    },
                },
            },
            {
                "name": "stop_screen_share",
                "description": "Stop seeing the user's screen once you have finished looking.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                },
            },
        ]

        # Everything except the model resource comes from the shared builder, so
        # this session and the browser relay cannot drift apart: response
        # modalities inside generationConfig, media resolution, the sliding
        # context window, both transcripts, and the optional search tool.
        if google_search is None:
            google_search = self._grounding_enabled
        return gemini_live.build_setup(
            self.config,
            model=model_resource,
            system_instruction=system_text,
            google_search=bool(google_search),
            declarations=declarations,
        )

    def _model_candidates(self, use_api_key: bool) -> list[str]:
        """Return configured model first, then compatible recovery models.

        ``--live-model`` and ``live_voice.model`` used to be ignored whenever
        API-key authentication was enabled because that branch began with a
        hard-coded list.  Keeping the configured value first also makes model
        failures diagnosable instead of silently running a different model.
        """
        configured = RETIRED_LIVE_MODELS.get(
            (self.config.model or "").strip(), (self.config.model or "").strip()
        )
        if configured != (self.config.model or "").strip():
            log.warn(
                f"live_voice.model names '{self.config.model}', which the Live API no "
                f"longer serves; using '{configured}' instead."
            )
        if use_api_key:
            # gemini-3.8-live is the model this project runs (the Google AI
            # Studio template's), so it is also the first fallback: a session
            # must not quietly end up on an older preview when the configured
            # name is unavailable.
            fallbacks = [
                "models/gemini-3.8-live",
                "models/gemini-2.5-flash-native-audio-latest",
                "models/gemini-2.5-flash-native-audio-preview-12-2025",
                "models/gemini-2.5-flash-native-audio-preview-09-2025",
                "models/gemini-3.1-flash-live-preview",
            ]
        else:
            fallbacks = [
                "gemini-2.0-flash-exp",
                "gemini-2.0-flash-realtime-exp",
                "gemini-2.0-flash",
                "gemini-3.1-flash-live",
            ]
        candidates = [RETIRED_LIVE_MODELS.get(model, model) for model in [configured, *fallbacks]]
        candidates = list(dict.fromkeys([model for model in candidates if model]))
        # A model that accepted a session and ignored the microphone for this key
        # must not stay first just because it is the configured one: that session
        # opens, hears nothing and never answers, which is the failure this
        # package measured on gemini-3.8-live (see gemini_live._AUDIO_DEAF).
        if use_api_key:
            key = self._api_key()
            hearing = [model for model in candidates if not gemini_live.audio_deaf(model, key)]
            deaf = [model for model in candidates if gemini_live.audio_deaf(model, key)]
            return hearing + deaf
        return candidates

    async def _disconnect(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._is_connected = False

    async def _run_loop(self, started_event: threading.Event) -> None:
        started_event.set()
        retry_delay = 1.0

        api_key = self._api_key()
        backend = (self.config.backend or "api_key").strip().lower()
        use_api_key = backend == "api_key"
        if use_api_key and not api_key:
            log.error("Gemini Live is configured for API-key authentication, but no API key is available. "
                      "Set JARVIS_LIVE_API_KEY or GEMINI_API_KEY.")
            self._is_running = False
            return

        candidates = self._model_candidates(use_api_key)

        model_idx = 0
        logged_fallback_notice = False

        while self._is_running:
            current_model = candidates[model_idx % len(candidates)]
            self._active_model = current_model
            try:
                ws_url = (
                    getattr(self.config, "ws_url", "")
                    or os.environ.get("JARVIS_LIVE_WS_URL")
                    or os.environ.get("JARVIS_REALTIME_URL")
                    or ""
                )
                if ws_url:
                    uri = ws_url
                    headers = None
                    project_id = ""
                elif use_api_key:
                    uri = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}"
                    headers = None
                    project_id = ""
                else:
                    token, project_id = self._get_auth_token_and_project()
                    loc = self.config.location or "us-central1"
                    host = f"{loc}-aiplatform.googleapis.com" if loc != "global" else "aiplatform.googleapis.com"
                    uri = f"wss://{host}/ws/google.cloud.aiplatform.v1beta1.LlmBidiService/BidiGenerateContent"
                    headers = {"Authorization": f"Bearer {token}"}

                log.debug(f"Connecting to Live WebSocket ({current_model}) at {uri[:40]}...")

                async with websockets.connect(
                    uri,
                    additional_headers=headers,
                    open_timeout=8.0,
                    ping_interval=None,
                    ping_timeout=None,
                ) as ws:
                    self._ws = ws
                    retry_delay = 1.0

                    # 1. Send setup handshake
                    setup_msg = self._build_setup_message(project_id, model_name=current_model)
                    await ws.send(json.dumps(setup_msg))

                    # 2. Run concurrent sender and receiver coroutines
                    recv_task = asyncio.create_task(self._recv_loop(ws))
                    send_task = asyncio.create_task(self._send_loop(ws))

                    done, pending = await asyncio.wait(
                        [recv_task, send_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()

                    for task in done:
                        exc = task.exception()
                        if exc:
                            raise exc

            except websockets.exceptions.ConnectionClosed as cc_exc:
                self._is_connected = False
                if self._is_running:
                    is_1008 = getattr(cc_exc, "rcvd", None) and getattr(cc_exc.rcvd, "code", None) == 1008
                    if is_1008 and model_idx < len(candidates) - 1:
                        model_idx += 1
                        await asyncio.sleep(0.2)
                        continue

                    # A refusal that mentions quota is ambiguous: either the
                    # Live allowance is spent or the Google Search tool was
                    # rejected. Retrying the identical setup without that tool
                    # is the only way to tell, and it costs one handshake.
                    if self._grounding_enabled and gemini_live.quota_refusal(str(cc_exc)):
                        self._grounding_enabled = False
                        self.grounding_blocked = str(cc_exc)
                        log.warn("Gemini Live refused the handshake with a quota error while "
                                 "Google Search grounding was offered; retrying without it.")
                        self._remember_grounding_failure(str(cc_exc))
                        await asyncio.sleep(0.2)
                        continue

                    if not logged_fallback_notice:
                        log.warn(f"Gemini Live connection closed: {cc_exc}. Retrying automatically.")
                        logged_fallback_notice = True

                    await asyncio.sleep(60.0)

            except Exception as exc:
                self._is_connected = False
                if self._is_running:
                    if self._grounding_enabled and gemini_live.quota_refusal(str(exc)):
                        self._grounding_enabled = False
                        self.grounding_blocked = str(exc)
                        log.warn("Gemini Live refused the handshake with a quota error while "
                                 "Google Search grounding was offered; retrying without it.")
                        self._remember_grounding_failure(str(exc))
                        await asyncio.sleep(0.2)
                        continue
                    if not logged_fallback_notice:
                        log.warn(f"Gemini Live connection failed: {exc}. Retrying automatically.")
                        logged_fallback_notice = True
                    await asyncio.sleep(60.0)

    def _remember_grounding_failure(self, reason: str) -> None:
        """Let the voice report say why there is no web search this session."""
        try:
            from . import readiness

            readiness.remember_failure(
                "gemini_grounding",
                "Google Search grounding was refused for this key (a paid-plan feature), "
                f"so live voice runs without web search: {str(reason)[:160]}",
            )
        except Exception:
            pass

    async def _send_loop(self, ws: Any) -> None:
        while self._is_running:
            try:
                msg = await self._outbound_queue.get()
                if msg is None:
                    break
                await ws.send(json.dumps(msg))
                self._outbound_queue.task_done()
            except asyncio.CancelledError:
                break

    async def _recv_loop(self, ws: Any) -> None:
        while self._is_running:
            try:
                raw = await ws.recv()
                if not raw:
                    continue
                data = json.loads(raw)
                self._handle_server_message(data)
            except asyncio.CancelledError:
                break

    def _handle_server_message(self, data: dict[str, Any]) -> None:
        if "setupComplete" in data or "setup_complete" in data:
            self._is_connected = True
            log.ok(f"Gemini Real-Time Live Voice session established ({getattr(self, '_active_model', 'Live Model')}).")

        # 1. Server Content (Audio / Text turns)
        server_content = data.get("serverContent") or data.get("server_content")
        if server_content:
            self._is_connected = True
            if server_content.get("interrupted"):
                if self.on_interrupted is not None:
                    try:
                        self.on_interrupted()
                    except Exception:
                        pass

            model_turn = server_content.get("modelTurn") or server_content.get("model_turn", {})
            parts = model_turn.get("parts", [])
            for part in parts:
                if not isinstance(part, dict):
                    continue

                # Audio output chunk
                inline_data = part.get("inlineData") or part.get("inline_data")
                if inline_data and isinstance(inline_data, dict):
                    raw_b64 = inline_data.get("data")
                    if raw_b64:
                        try:
                            pcm_chunk = base64.b64decode(raw_b64)
                            if self.on_audio_out is not None:
                                self.on_audio_out(pcm_chunk)
                        except Exception as decode_exc:
                            log.debug(f"Error decoding output audio: {decode_exc}")

                # Text transcript chunk
                text = part.get("text")
                if text and self.on_text is not None:
                    try:
                        self.on_text(text)
                    except Exception:
                        pass

            # Native audio output normally arrives only in inlineData. The
            # transcript is a sibling field, not a model-turn text part.
            transcription = (
                server_content.get("outputTranscription")
                or server_content.get("output_transcription")
            )
            transcript_text = transcription.get("text") if isinstance(transcription, dict) else ""
            if transcript_text and self.on_text is not None:
                try:
                    self.on_text(transcript_text)
                except Exception:
                    pass

        # 2. Tool Calls
        tool_call = data.get("toolCall") or data.get("tool_call")
        if tool_call and isinstance(tool_call, dict):
            self._is_connected = True
            calls = tool_call.get("functionCalls") or tool_call.get("function_calls") or []
            for call in calls:
                if not isinstance(call, dict):
                    continue
                name = call.get("name", "")
                args = call.get("args", {})
                call_id = call.get("id", "")
                if self.on_tool_call is not None:
                    try:
                        response_output = self.on_tool_call(name, args, call_id)
                        if response_output is not None:
                            self.send_tool_response(call_id, response_output, name)
                    except Exception as tool_exc:
                        log.warn(f"Tool call '{name}' handler failed: {tool_exc}")
                        self.send_tool_response(
                            call_id,
                            {"status": "error", "error": str(tool_exc)},
                            name,
                        )
