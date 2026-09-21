"""Configuration loading.

Order of precedence (highest first):
  1. environment variables (``JARVIS_*``)
  2. ``config.yaml`` next to the project root
  3. built-in defaults below

The defaults are chosen for a modest machine (RTX 2050 / 4 GB VRAM / 8 GB RAM):
a small quantised model served by Ollama, with graceful fallbacks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .utils.paths import project_root

# The project root, from the one module that owns that answer. Import stays
# local: paths has no dependencies, so this cannot cycle back into config.
ROOT = project_root()
CONFIG_PATH = ROOT / "config.yaml"


@dataclass
class BrainConfig:
    # backend: "gemini" | "ollama" | "hf" | "llamacpp" | "openai" | "anthropic"
    #   gemini - Google Cloud Vertex AI (default)
    #   ollama - local server
    #   hf     - load a HuggingFace model + your trained LoRA adapter directly,
    #            no server needed. Best way to run your fine-tune immediately.
    backend: str = "gemini"
    # Model name/tag. For gemini this is e.g. "gemini-3.8-flash".
    # For ollama this is the pulled model, e.g. "ornith:9b".
    # For hf this is the base model id, e.g. "Qwen/Qwen2.5-0.5B-Instruct".
    # Once you fine-tune, point this at "jarvis" (see training/export_ollama.py).
    model: str = "gemini-3.8-flash"
    # Google Cloud Vertex AI region/location (used by "gemini" backend).
    location: str = "global"
    # Path to a trained LoRA adapter (used by the "hf" backend). Empty = base only.
    adapter_path: str = ""
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"   # used by openai/anthropic backends
    api_key: str = ""                     # direct API key value from env/config
    temperature: float = 0.2
    max_tokens: int = 50000
    # If true, send the annotated screenshot to a vision model each step.
    # Defaults to True for Gemini backend.
    use_vision: bool = True
    request_timeout: int = 120
    # Let Jarvis answer plain conversation (greetings, small talk, general
    # questions) directly, with no tools/perception. Set false to force every
    # input through the computer-control loop.
    conversational: bool = True
    # Microsoft Azure AI Foundry Agent settings (GPT-6 Astra)
    foundry_endpoint: str = "https://satviksingh-resource.services.ai.azure.com/api/projects/satviksingh"
    foundry_agent_name: str = "gpt-6"
    foundry_agent_version: str = "1"
    azure_tenant_id: str = ""


@dataclass
class PerceptionConfig:
    # Use the Windows UI Automation tree to label elements (fast, no GPU).
    use_uia: bool = True
    # OCR fallback for apps with no accessibility info (icon buttons, canvases,
    # games) and for the voice agent's click_target when the accessibility tree
    # cannot name a control. Windows' own OCR engine is tried first, so this no
    # longer implies a torch dependency.
    use_ocr: bool = True
    # Cap on labelled elements handed to the model, to keep the prompt small.
    max_elements: int = 60
    # Save annotated screenshots for debugging / dataset collection.
    save_screenshots: bool = True


@dataclass
class SafetyConfig:
    # Ask for confirmation before each action (recommended while learning).
    confirm_each_action: bool = False
    # Hard stop after this many steps to avoid runaway loops.
    max_steps: int = 50
    # Refuse shell commands matching these (case-insensitive substrings).
    blocked_command_patterns: tuple[str, ...] = (
        "format ", "del /", "rmdir /s", "rm -rf", "diskpart", "mkfs",
        ":(){", "shutdown", "reg delete", "cipher /w",
    )
    # Directories writes/deletes are confined to (empty = home dir only guard off).
    allow_paths: tuple[str, ...] = ()
    # Autonomous self-healing with Tree-of-Thought (ToT) error recovery
    self_healing: bool = True
    # Maximum exploration depth for ToT recovery branching
    tot_max_depth: int = 3
    # Maximum automated recovery interventions per task
    max_healing_attempts: int = 3
    # Automatically refocus or un-minimize target window on focus loss
    auto_refocus: bool = True


@dataclass
class DataConfig:
    # Log every (observation, action) step to build a real agentic dataset.
    collect_trajectories: bool = True
    trajectory_dir: str = "dataset/data/trajectories"
    # Reinforcement learning: after the model claims a task is finished, run a
    # verification pass (model judges the final screen state) before rewarding
    # the plan. Prevents fake successes from polluting memory.txt.
    verify_success: bool = True
    # Cap on memory.txt size (chars). Oldest learned entries are dropped first
    # so the system prompt never bloats.
    memory_max_chars: int = 4000
    # Conversational memory: how many recent (user prompt, Jarvis response)
    # exchanges to feed back in for continuity across tasks/sessions.
    chat_history_turns: int = 10


@dataclass
class VoiceConfig:
    # TTS engine: "kokoro" = local Kokoro-82M ONNX in models/tts/ (offline default);
    # "sapi" = Windows native SAPI5 (offline fallback);
    # "edge" = Microsoft Edge Neural TTS (online free);
    # "gemini" = Gemini TTS on Vertex AI (cloud);
    # "azure" = Microsoft Azure Cognitive Services Speech SDK;
    # "fish" = Fish Audio API (https://fish.audio);
    # "foundry" = OpenAI-compatible /v1/audio/speech relay.
    engine: str = "kokoro"
    # Azure Cognitive Services Speech SDK configuration
    azure_speech_endpoint: str = "https://satviksingh-resource.cognitiveservices.azure.com/"
    azure_speech_key: str = ""
    azure_speech_voice: str = "en-US-OnyxTurboMultilingualNeural"
    # OpenAI / Foundry Relay TTS configuration
    tts_endpoint: str = "http://localhost:8000/v1/audio/speech"
    tts_model: str = "tts-1"
    tts_voice: str = "en-US-OnyxTurboMultilingualNeural"
    # Kokoro voice id: bm_george/bm_lewis = British male (movie-JARVIS feel),
    # af_heart = highest-quality American female. Speed 1.0 = natural pace.
    local_voice: str = "bm_george"
    local_speed: float = 1.0
    # Gemini TTS is deliberately separate from the thinking model above.
    model: str = "gemini-3.1-flash-tts-preview"
    voice: str = "Kore"
    language_code: str = "en-US"
    # Fast, non-agentic model used only for speech-to-text.
    transcription_model: str = "gemini-2.5-flash-lite"
    # Stop recording promptly after the user finishes speaking.
    silence_after: float = 0.6
    # Full-duplex voice with real-time interruption (barge-in): allows simultaneous
    # speaking and listening. The user can interrupt Jarvis mid-sentence.
    full_duplex: bool = True
    # Barge-in sensitivity: 0.1 (strict, needs louder voice) to 1.0 (sensitive).
    barge_in_sensitivity: float = 0.5
    # Fish Audio TTS settings (https://fish.audio)
    fish_audio_key: str = ""
    fish_audio_voice_id: str = ""  # custom reference_id/voice model ID
    fish_audio_model: str = "s2.1-pro"
    fish_audio_latency: str = "normal"  # "normal" | "balanced"
    # Frames (~0.1s each) of sustained speech required to trigger barge-in cutoff.
    barge_in_hold: int = 2



@dataclass
class LiveVoiceConfig:
    # Real-Time Gemini Live Voice model (multimodal bidi live session via API key or gcloud / Vertex AI)
    enabled: bool = False
    # Which engine the live voice button runs:
    #   gemini - Gemini Live, the browser streaming 16kHz audio through the
    #            loopback relay (default)
    #   fish   - hosted Fish Audio Agents session (needs live_voice.fish_agent_id)
    #   relay  - your own OpenAI-Realtime-compatible endpoint at ws_url
    #   auto   - gemini if a Gemini key is present, else fish, else relay
    provider: str = "gemini"
    # Must name a *live* model. gemini-3.8-live is what the Google AI Studio
    # template uses; `python run.py --check` and the voice report name what the
    # configured key can actually open.
    model: str = "gemini-3.8-live"
    # Algenib is the template's voice; the older Aoede/Puck/Charon/Kore/Fenrir
    # names still work.
    voice_name: str = "Algenib"
    # How much detail each video frame carries. MEDIUM is the template's choice.
    media_resolution: str = "medium"  # low | medium | high
    # Google Search grounding for the voice agent. `auto` and `on` both try it;
    # an account without grounding quota answers the handshake with a 1011 quota
    # error, so the relay retries without it rather than losing the session.
    google_search: str = "auto"  # auto | on | off
    # Sliding-window context compression, so a long conversation does not end by
    # running into the model's context limit mid-sentence.
    context_window_compression: bool = True
    context_trigger_tokens: int = 104857
    context_sliding_tokens: int = 52428
    # Lets the relay continue a session on a fresh socket after the server's
    # periodic disconnect instead of dropping the conversation.
    session_resumption: bool = True
    # Screen sharing: the voice agent asks for it (share_screen tool) and the
    # browser streams ~1 frame/second through the open relay while it is on.
    # Frames leave the machine, so this is the switch that authorises it.
    screen_share: bool = True
    screen_share_interval: float = 1.0
    screen_share_max_dim: int = 1024
    screen_share_quality: int = 60
    location: str = "us-central1"
    backend: str = "api_key"  # api_key | gcloud
    api_key: str = ""
    ws_url: str = ""  # e.g. ws://localhost:8000/v1/realtime?api_key=SATVIKNOOB
    # Fish Audio Agents (hosted realtime voice agent, https://fish.audio/app/agents).
    # When set, the browser voice mode can open a Fish session and let the hosted
    # agent drive the local main worker agent through client tools. Auth uses the
    # Fish Audio API key (FISH_AUDIO_API_KEY / credential vault).
    fish_agent_id: str = ""
    fish_api_base: str = ""
    # Hosted agent's own LLM. It runs the conversation AND decides when to call
    # a tool, so it is the single biggest lever on whether "open Spotify"
    # actually happens. Applied by `python -m jarvis.live.fish_agents`; blank
    # leaves the agent's own setting alone.
    fish_llm_model: str = "google/gemini-3.5-flash-lite"
    # Proactive mid-task in-progress narration: voice model monitors what the
    # text-based model is doing and informs the user what has been done and what
    # will be done next in mid task.
    narrate_steps: bool = True
    narration_verbosity: str = "normal"  # "brief" | "normal" | "detailed"
    sample_rate_in: int = 16000
    sample_rate_out: int = 24000
    full_duplex: bool = True
    barge_in_sensitivity: float = 0.5


@dataclass
class RemoteConfig:
    # Public HTTPS URL of the separately hosted Jarvis Remote relay. Leaving
    # this blank keeps remote support disabled until the owner opts in.
    relay_url: str = ""
    # Where this device keeps its private pairing material. Empty uses the
    # per-user ~/.jarvis_remote directory, outside the project/repository.
    state_dir: str = ""
    # Time a controller waits for a remote task result (5-600 seconds).
    result_timeout: int = 180
    # A trusted remote agent runs tasks unattended. Set this to true on a
    # particular device to require somebody there to approve every action.
    require_confirmation: bool = False


@dataclass
class BrowserConfig:
    # Run browser headless (silent background) or headful (visible window).
    headless: bool = False
    # Browser engine: "chromium" | "chrome" | "msedge" | "firefox" | "webkit"
    browser_type: str = "chromium"
    # Optional Chrome DevTools Protocol URL (e.g. "http://localhost:9222") to attach
    # to an already running browser instance.
    cdp_url: str = ""
    # Persistent user data profile path (keeps cookies, logins across sessions).
    # Default is ~/.jarvis/browser_profile.
    user_data_dir: str = ""
    viewport_width: int = 1280
    viewport_height: int = 800
    timeout: int = 30000


@dataclass
class DaemonConfig:
    # Proactive background daemon: monitors battery, system resources, file changes,
    # and proactive daily routines.
    enabled: bool = True
    # Background monitoring check frequency in seconds
    check_interval: float = 10.0
    # Battery and power status monitoring
    battery_monitoring: bool = True
    battery_threshold_low: int = 20
    # CPU & RAM utilization monitoring
    resource_monitoring: bool = True
    cpu_threshold_high: int = 90
    memory_threshold_high: int = 85
    # Directory file monitoring (e.g. downloads folder)
    file_monitoring: bool = True
    watch_directories: tuple[str, ...] = ("~/Downloads",)
    # Proactive daily routines
    morning_briefing_enabled: bool = True
    morning_briefing_time: str = "09:00"
    evening_summary_enabled: bool = True
    evening_summary_time: str = "21:00"
    # Speak proactive notifications aloud if voice is enabled
    voice_announcements: bool = True


@dataclass
class HudConfig:
    # Global Floating Mini HUD & System-Wide Hotkey overlay
    enabled: bool = True
    always_on_top: bool = True
    hotkey_toggle: str = "ctrl+alt+j"
    hotkey_voice: str = "alt+v"
    hotkey_vision: str = "ctrl+alt+s"
    hotkey_macro: str = "ctrl+alt+r"
    hotkey_stop: str = "ctrl+alt+x"
    opacity: float = 0.94
    # Default position on desktop: "bottom_right", "top_right", "bottom_left", "top_center", "center"
    position: str = "bottom_right"


@dataclass
class ShadowConfig:
    # Shadow Desktop & Virtual Workspace Execution (Ghost Automation)
    # When enabled, Jarvis launches applications and performs clicks/typing in an
    # isolated, hidden Win32 desktop without hijacking the user's physical mouse/keyboard.
    enabled: bool = False
    desktop_name: str = "JarvisShadowDesktop"
    headless: bool = True
    promote_on_finish: bool = False
    pip_stream: bool = True
    resolution: tuple[int, int] = (1920, 1080)


@dataclass
class Config:
    brain: BrainConfig = field(default_factory=BrainConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    data: DataConfig = field(default_factory=DataConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    live_voice: LiveVoiceConfig = field(default_factory=LiveVoiceConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    hud: HudConfig = field(default_factory=HudConfig)
    shadow: ShadowConfig = field(default_factory=ShadowConfig)
    # Voice mode: speak replies aloud (Gemini TTS) and allow spoken commands
    # (mic -> Gemini transcription). Toggle at runtime with ':voice on|off'.
    voice_enabled: bool = False
    # Hands-free wake-word mode: launch straight into "say Hey Jarvis" mode.
    # Not persisted - set only via --wake / JARVIS_WAKE; toggle at runtime
    # with ':wake'.
    wake_enabled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)



def _apply(dc: Any, values: dict[str, Any]) -> None:
    for key, val in (values or {}).items():
        if hasattr(dc, key):
            setattr(dc, key, val)


def load_config(path: Path | str | None = None) -> Config:
    # Load .env file if it exists
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        # Fallback manual parser to prevent new dependencies
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v

    cfg = Config()
    p = Path(path) if path else CONFIG_PATH
    if p.exists():
        data = _read_yaml(p)
        _apply(cfg.brain, data.get("brain", {}))
        _apply(cfg.perception, data.get("perception", {}))
        _apply(cfg.safety, data.get("safety", {}))
        _apply(cfg.data, data.get("data", {}))
        _apply(cfg.voice, data.get("voice", {}))
        _apply(cfg.live_voice, data.get("live_voice", {}))
        _apply(cfg.remote, data.get("remote", {}))
        _apply(cfg.browser, data.get("browser", {}))
        _apply(cfg.daemon, data.get("daemon", {}))
        _apply(cfg.hud, data.get("hud", {}))
        _apply(cfg.shadow, data.get("shadow", {}))
        cfg.voice_enabled = bool(data.get("voice_enabled", cfg.voice_enabled))

    # Environment overrides for the most common knobs.
    env = os.environ
    if v := env.get("JARVIS_BROWSER_HEADLESS"):
        cfg.browser.headless = v.lower() in {"1", "true", "yes"}
    if v := env.get("JARVIS_SHADOW"):
        cfg.shadow.enabled = v.lower() in {"1", "true", "yes"}

    if v := env.get("JARVIS_BROWSER_CDP"):
        cfg.browser.cdp_url = v
    if v := env.get("JARVIS_BACKEND") or env.get("BACKEND"):
        cfg.brain.backend = v
    if v := env.get("JARVIS_MODEL") or env.get("MODEL_ID") or env.get("MODEL"):
        cfg.brain.model = v
    elif cfg.brain.backend in {"foundry", "azure", "azure-foundry", "azure_foundry", "foundry-agent"}:
        cfg.brain.model = "gpt-6"
    if v := env.get("JARVIS_FOUNDRY_ENDPOINT") or env.get("AZURE_FOUNDRY_ENDPOINT") or env.get("AZURE_AI_ENDPOINT"):
        cfg.brain.foundry_endpoint = v
    if v := env.get("JARVIS_AGENT_NAME") or env.get("AZURE_AGENT_NAME"):
        cfg.brain.foundry_agent_name = v
    if v := env.get("JARVIS_AGENT_VERSION") or env.get("AZURE_AGENT_VERSION"):
        cfg.brain.foundry_agent_version = v
    if v := env.get("AZURE_TENANT_ID"):
        cfg.brain.azure_tenant_id = v
    if v := env.get("AZURE_API_KEY") or env.get("AZURE_AI_KEY"):
        if not cfg.brain.api_key:
            cfg.brain.api_key = v
    if v := env.get("JARVIS_BASE_URL") or env.get("BASE_URL"):
        cfg.brain.base_url = v
    if v := env.get("JARVIS_LOCATION") or env.get("LOCATION"):
        cfg.brain.location = v
    if v := env.get("JARVIS_TTS_ENGINE"):
        cfg.voice.engine = "kokoro" if v.lower() == "local" else v
    if getattr(cfg.voice, "engine", "").lower() == "local":
        cfg.voice.engine = "kokoro"
    if v := env.get("JARVIS_TTS_ENDPOINT") or env.get("TTS_ENDPOINT"):
        cfg.voice.tts_endpoint = v
    if v := env.get("JARVIS_AZURE_SPEECH_ENDPOINT") or env.get("AZURE_SPEECH_ENDPOINT"):
        cfg.voice.azure_speech_endpoint = v
        if not env.get("JARVIS_TTS_ENDPOINT") and not env.get("TTS_ENDPOINT"):
            cfg.voice.tts_endpoint = v
    if v := env.get("JARVIS_AZURE_SPEECH_KEY") or env.get("AZURE_SPEECH_KEY") or env.get("SPEECH_KEY"):
        cfg.voice.azure_speech_key = v
    if v := env.get("JARVIS_AZURE_SPEECH_VOICE") or env.get("AZURE_SPEECH_VOICE") or env.get("SPEECH_VOICE"):
        cfg.voice.azure_speech_voice = v
    if v := env.get("JARVIS_TTS_MODEL") or env.get("TTS_MODEL"):
        cfg.voice.tts_model = v
        cfg.voice.model = v
    if v := env.get("JARVIS_TTS_VOICE") or env.get("TTS_VOICE"):
        cfg.voice.tts_voice = v
        cfg.voice.voice = v
    if v := env.get("JARVIS_LOCAL_VOICE"):
        cfg.voice.local_voice = v
    if v := env.get("JARVIS_LOCAL_SPEED"):
        try:
            cfg.voice.local_speed = float(v)
        except ValueError:
            pass
    if v := env.get("JARVIS_TTS_MODEL"):
        cfg.voice.model = v
    if v := env.get("JARVIS_TTS_VOICE"):
        cfg.voice.voice = v
    if v := env.get("JARVIS_TTS_LANGUAGE"):
        cfg.voice.language_code = v
    if v := env.get("JARVIS_STT_MODEL"):
        cfg.voice.transcription_model = v
    if v := env.get("JARVIS_VOICE_DUPLEX"):
        cfg.voice.full_duplex = v.lower() in {"1", "true", "yes"}
    if v := env.get("JARVIS_BARGE_IN_SENSITIVITY"):
        try:
            cfg.voice.barge_in_sensitivity = float(v)
        except ValueError:
            pass
    if v := env.get("JARVIS_FISH_AUDIO_API_KEY") or env.get("FISH_AUDIO_API_KEY") or env.get("FISH_API_KEY"):
        cfg.voice.fish_audio_key = v
    if v := env.get("JARVIS_FISH_AUDIO_VOICE_ID") or env.get("FISH_AUDIO_VOICE_ID") or env.get("FISH_AUDIO_REFERENCE_ID"):
        cfg.voice.fish_audio_voice_id = v
    if v := env.get("JARVIS_FISH_AUDIO_MODEL") or env.get("FISH_AUDIO_MODEL"):
        cfg.voice.fish_audio_model = v
    if v := env.get("JARVIS_FISH_AUDIO_LATENCY") or env.get("FISH_AUDIO_LATENCY"):
        cfg.voice.fish_audio_latency = v
    if v := env.get("JARVIS_LIVE_WS_URL") or env.get("JARVIS_REALTIME_URL") or env.get("REALTIME_URL") or env.get("LIVE_WS_URL"):
        cfg.live_voice.ws_url = v
    if v := env.get("JARVIS_FISH_AGENT_ID") or env.get("FISH_AGENT_ID"):
        cfg.live_voice.fish_agent_id = v
    if v := env.get("JARVIS_FISH_API_BASE") or env.get("FISH_AUDIO_API_BASE"):
        cfg.live_voice.fish_api_base = v
    if v := env.get("JARVIS_LIVE_MODEL"):
        cfg.live_voice.model = v
    if v := env.get("JARVIS_LIVE_PROVIDER"):
        cfg.live_voice.provider = v
    if v := env.get("JARVIS_LIVE_GOOGLE_SEARCH"):
        cfg.live_voice.google_search = v
    if v := env.get("JARVIS_LIVE_MEDIA_RESOLUTION"):
        cfg.live_voice.media_resolution = v
    if v := env.get("JARVIS_LIVE_SCREEN_SHARE"):
        cfg.live_voice.screen_share = v.lower() in {"1", "true", "yes", "on"}
    if v := env.get("JARVIS_LIVE_VOICE"):
        cfg.live_voice.voice_name = v
    if v := env.get("JARVIS_LIVE_LOCATION"):
        cfg.live_voice.location = v
    if v := env.get("JARVIS_LIVE_NARRATE"):
        cfg.live_voice.narrate_steps = v.lower() in {"1", "true", "yes"}
    if v := env.get("JARVIS_LIVE_VERBOSITY"):
        cfg.live_voice.narration_verbosity = v
    if v := env.get("JARVIS_LIVE_BACKEND"):
        cfg.live_voice.backend = v
    if v := env.get("JARVIS_LIVE_API_KEY") or env.get("GEMINI_LIVE_API_KEY") or env.get("GEMINI_API_KEY"):
        cfg.live_voice.api_key = v
        cfg.live_voice.backend = "api_key"
    if v := env.get("JARVIS_REMOTE_URL"):
        cfg.remote.relay_url = v
    if v := env.get("JARVIS_REMOTE_STATE_DIR"):
        cfg.remote.state_dir = v
    if v := env.get("JARVIS_API_KEY") or env.get("API_KEY") or env.get("FOUNDRY_API_KEY") or env.get("OPENROUTER_API_KEY") or env.get("OPENAI_API_KEY") or env.get("GEMINI_API_KEY"):
        cfg.brain.api_key = v
        if env.get("OPENROUTER_API_KEY"):
            os.environ["OPENROUTER_API_KEY"] = env.get("OPENROUTER_API_KEY")
        if env.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = env.get("OPENAI_API_KEY")
        if env.get("GEMINI_API_KEY"):
            os.environ["GEMINI_API_KEY"] = env.get("GEMINI_API_KEY")
        elif v.startswith("AIzaSy"):
            os.environ["GEMINI_API_KEY"] = v
        elif v.startswith("sk-or-"):
            os.environ["OPENROUTER_API_KEY"] = v
            # Mirrored for the code paths that only know the OpenAI name, but
            # never over an OPENAI_API_KEY the user set themselves: that would
            # hand an OpenRouter key to api.openai.com and report it as an
            # invalid OpenAI key.
            os.environ.setdefault("OPENAI_API_KEY", v)
        else:
            os.environ.setdefault("OPENAI_API_KEY", v)
    else:
        try:
            from .security import get_secret
            if vault_key := (get_secret("OPENROUTER_API_KEY") or get_secret("JARVIS_API_KEY") or get_secret("GEMINI_API_KEY") or get_secret("OPENAI_API_KEY") or get_secret("ANTHROPIC_API_KEY")):
                cfg.brain.api_key = vault_key
                if vault_key.startswith("sk-or-") or get_secret("OPENROUTER_API_KEY"):
                    os.environ["OPENROUTER_API_KEY"] = get_secret("OPENROUTER_API_KEY") or vault_key
                    # Same reason as above: the mirror is for compatibility, so
                    # it must not replace a key the environment already carries.
                    os.environ.setdefault("OPENAI_API_KEY", os.environ["OPENROUTER_API_KEY"])
                elif vault_key.startswith("AIzaSy") or get_secret("GEMINI_API_KEY"):
                    os.environ["GEMINI_API_KEY"] = get_secret("GEMINI_API_KEY") or vault_key
                else:
                    os.environ.setdefault("OPENAI_API_KEY", vault_key)
        except Exception:
            pass

    # A key already in the vault outranks the configured backend. This used to
    # be skipped whenever `backend: gcloud` was set, which made the whole branch
    # unreachable for exactly the users who needed it: a stored AI Studio key is
    # the free-tier path, while gcloud routes to Vertex, whose quotas are spent
    # against the project rather than a free allowance. The result was live
    # voice failing on a 429 - or on another provider's billing - while a usable
    # key sat unread in the vault.
    if not cfg.live_voice.api_key:
        try:
            from .security import get_secret
            if live_key := (get_secret("JARVIS_LIVE_API_KEY") or get_secret("GEMINI_API_KEY") or get_secret("GOOGLE_API_KEY")):
                cfg.live_voice.api_key = live_key
                cfg.live_voice.backend = "api_key"
        except Exception:
            pass

    # OpenRouter defaults
    if cfg.brain.backend.lower() == "openrouter":
        if not cfg.brain.base_url:
            cfg.brain.base_url = "https://openrouter.ai/api/v1"
        cfg.brain.api_key_env = "OPENROUTER_API_KEY"

    # Automatic fallback to openai/openrouter backend if custom URL or API key is set
    # but backend is still the default ollama
    if cfg.brain.backend == "ollama":
        has_custom_key = bool(cfg.brain.api_key)
        has_custom_url = cfg.brain.base_url and "11434" not in cfg.brain.base_url
        if has_custom_key or has_custom_url:
            if env.get("OPENROUTER_API_KEY") or (cfg.brain.api_key and cfg.brain.api_key.startswith("sk-or-")):
                cfg.brain.backend = "openrouter"
                if not cfg.brain.base_url:
                    cfg.brain.base_url = "https://openrouter.ai/api/v1"
                cfg.brain.api_key_env = "OPENROUTER_API_KEY"
            elif env.get("GEMINI_API_KEY") or (cfg.brain.api_key and cfg.brain.api_key.startswith("AIzaSy")):
                cfg.brain.backend = "gemini"
            else:
                cfg.brain.backend = "openai"

    if env.get("JARVIS_VISION") in {"0", "false", "False"}:
        cfg.brain.use_vision = False
    elif env.get("JARVIS_VISION") in {"1", "true", "True"}:
        cfg.brain.use_vision = True
    if env.get("JARVIS_VOICE") in {"1", "true", "True"}:
        cfg.voice_enabled = True
    if env.get("JARVIS_LIVE") in {"1", "true", "True"}:
        cfg.live_voice.enabled = True
    if env.get("JARVIS_WAKE") in {"1", "true", "True"}:
        cfg.wake_enabled = True
    if env.get("JARVIS_CONFIRM") in {"1", "true", "True"}:
        cfg.safety.confirm_each_action = True
    return cfg


def _read_yaml(p: Path) -> dict:
    try:
        import yaml  # type: ignore

        with p.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        # Fall back to a minimal parser so the app runs before deps install.
        return _read_yaml_minimal(p)


def _read_yaml_minimal(p: Path) -> dict:
    """A tiny nested key: value reader for the simple config we ship.

    Only supports two levels of ``key:`` sections with scalar leaves - enough
    for config.yaml when PyYAML is not yet installed.
    """
    result: dict[str, dict] = {}
    section: str | None = None
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.rstrip().endswith(":"):
            section = line.strip()[:-1]
            result[section] = {}
        elif section and ":" in line:
            k, _, v = line.strip().partition(":")
            result[section][k.strip()] = _coerce(v.strip())
    return result


def _coerce(v: str) -> Any:
    if v.lower() in {"true", "false"}:
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v.strip('"\'')
