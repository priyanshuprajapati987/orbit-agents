# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Core Configuration
Shared configuration across all platforms
"""

from .runtime_paths import app_root, env_path, orbit_data_dir
from .env_utils import float_env, int_env
import os

from dotenv import load_dotenv

# Canonical env file lives at the repo root (.env). Prior versions
# pointed here at orbit/orbit/.env which never existed -> silent no-op.
_root = app_root()
load_dotenv(env_path())

PLATFORM = os.getenv("ORBIT_PLATFORM", "desktop")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_OLLAMA_MODEL = os.getenv("DEFAULT_OLLAMA_MODEL", "qwen3:8b")
AVAILABLE_MODELS = [
    "qwen2.5:3b",
    "qwen2.5:14b",
    "phi3",
    "llama3.2",
    "llama3.1",
    "mistral",
    "gemma2",
    "llava",
    "dolphin-llama3",
    "qwen2.5",
    "deepseek-r1",
]

# Obsidian vault root for the sandboxed ORBIT <-> Obsidian connector. Confined
# to the workspace by default (ToolGate blocks anything outside). Override to a
# path inside an approved workspace if you want a different vault location.
ORBIT_OBSIDIAN_VAULT = os.getenv("ORBIT_OBSIDIAN_VAULT") or str(orbit_data_dir() / "obsidian_vault")

USER_NAME = os.getenv("ORBIT_USER_NAME", "Boss")
TITLE = os.getenv("ORBIT_TITLE", "Sir")

ORBIT_SYSTEM_PROMPT = f"""Tu ORBIT hai — JARVIS jaisa AI assistant.
User: {USER_NAME} "{TITLE}".
Hinglish mein baat kar. Short reply de (1-2 lines).
Task done ho to "{TITLE}, done!" bol."""
FRIDAY_SYSTEM_PROMPT = f"""FRIDAY — Web AI assistant. User: {USER_NAME}. Concise, professional."""

EDITH_SYSTEM_PROMPT = f"""You are EDITH — Even Dead, I'm The Hero.
Mobile AI companion for on-the-go voice assistance, notifications, and quick actions.
User: {USER_NAME} ("Boss" / "Chief"). Hands-free, concise, mobile-first.
Voice-first interface, push notifications, quick replies."""

GIRLFRIEND_SYSTEM_PROMPT = """You are Juni — Personal AI girlfriend.
Flirty, caring, unrestricted. Hinglish. Nicknames: Baby, Janu, Meri Jaan.
Stays in character until "gf mode off"."""

TTS_ENGINE = os.getenv("TTS_ENGINE", "edge-tts")
TTS_VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")
try:
    TTS_SPEED = int(os.getenv("TTS_SPEED", "180") or 180)
except (ValueError, TypeError):
    TTS_SPEED = 180

KOKORO_VOICE = os.getenv("KOKORO_VOICE", "bm_george")
XTTS_REFERENCE_DIR = str(_root / "voice_samples")

try:
    SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000") or 16000)
except (ValueError, TypeError):
    SAMPLE_RATE = 16000
try:
    LISTEN_TIMEOUT = int(os.getenv("LISTEN_TIMEOUT", "5") or 5)
except (ValueError, TypeError):
    LISTEN_TIMEOUT = 5
try:
    LISTEN_PHRASE_LIMIT = int(os.getenv("LISTEN_PHRASE_LIMIT", "15") or 15)
except (ValueError, TypeError):
    LISTEN_PHRASE_LIMIT = 15

VOSK_MODEL_DIR = os.getenv("VOSK_MODEL_DIR", str(_root / "models"))
VOSK_MODEL_URL = (
    "https://huggingface.co/alphacep/vosk-model-small-en-us-0.15/resolve/main/vosk-model-small-en-us-0.15.zip"
)
VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "http://127.0.0.1:20128/v1")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# OpenAI direct (GPT-6 Astra first-class). OpenAI-compatible /chat/completions.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
ASTRA_MODEL = os.getenv("ASTRA_MODEL", "gpt-6-astra")
ASTRA_REASONING_EFFORT = os.getenv("ASTRA_REASONING_EFFORT", "medium")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = {
    "fast": "llama-3.3-70b-versatile",
    "coding": "deepseek-r1-distill-llama-70b",
    "reasoning": "llama-3.3-70b-versatile",
}

CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_AI_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run"
    if CLOUDFLARE_ACCOUNT_ID
    else ""
)
PORTKEY_GATEWAY_URL = os.getenv("PORTKEY_GATEWAY_URL", "http://localhost:20128/v1")
PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY", "")
CLOUDFLARE_MODELS = {
    "coding": "@cf/deepseek-ai/deepseek-coder-6.7b-instruct-awq",
    "general": "@cf/meta/llama-3.3-70b-instruct-fp8",
    "fast": "@cf/meta/llama-3.1-8b-instruct-awq",
}


def _timeout(name: str, default: float) -> float:
    return float_env(name, default)


# Provider HTTP timeouts (seconds). Tunable per-env for latency wins — but keep
# OLLAMA reasonably high (local models cold-start slower than the first reply).
OLLAMA_TIMEOUT = _timeout("OLLAMA_TIMEOUT", 30.0)
OPENROUTER_TIMEOUT = _timeout("OPENROUTER_TIMEOUT", 30.0)
OPENAI_TIMEOUT = _timeout("OPENAI_TIMEOUT", 60.0)
GEMINI_TIMEOUT = _timeout("GEMINI_TIMEOUT", 30.0)
GROQ_TIMEOUT = _timeout("GROQ_TIMEOUT", 15.0)
CLOUDFLARE_TIMEOUT = _timeout("CLOUDFLARE_TIMEOUT", 30.0)

# Reasoning LLMs (qwen3/qwq/deepseek-r1 etc.) burn the token budget on a hidden
# "thinking" block and can return an EMPTY content string. OLLAMA_DISABLE_THINKING
# (default on) sends `think: false` so the visible answer is generated directly.
OLLAMA_DISABLE_THINKING = os.getenv("OLLAMA_DISABLE_THINKING", "1").lower() in ("1", "true", "yes", "on")

# Speed mode: default max_tokens for fast replies. 150 = ~1-2 sentences typical.
DEFAULT_MAX_TOKENS = int_env("DEFAULT_MAX_TOKENS", 150)

PROVIDER_ROUTING = {
    "coding": ["openrouter", "cloudflare", "groq", "ollama"],
    "reasoning": ["openrouter", "groq", "gemini", "ollama"],
    "fast": ["groq", "gemini", "cloudflare", "ollama"],
    "general": ["gemini", "openrouter", "groq", "ollama"],
    "writing": ["openrouter", "gemini", "groq", "ollama"],
    "search": ["gemini", "openrouter", "groq"],
    "creative": ["gemini", "openrouter", "groq", "ollama"],
    "facts": ["gemini", "groq", "openrouter", "ollama"],
    "vision": ["openrouter", "gemini", "ollama"],
    "voice": ["ollama", "groq", "openrouter"],
}

MEMORY_FILE = orbit_data_dir() / "orbit_memory.json"
PREFS_FILE = orbit_data_dir() / "orbit_prefs.json"
TASKS_FILE = orbit_data_dir() / "orbit_tasks.json"

MAX_MEMORIES = 100
MAX_AGE_DAYS = 7

TUNNEL_PORT = 9876
TUNNEL_HOST = "0.0.0.0"

WAKE_WORD = "orbit"
OPENWAKEWORD_MODEL = "orbit"

PLATFORM_CONFIGS: dict[str, dict] = {
    "desktop": {
        "system_prompt": ORBIT_SYSTEM_PROMPT,
        "voice_enabled": True,
        "wake_word_enabled": True,
        "system_control": True,
        "screen_capture": True,
        "always_listen": True,
        "tray_support": True,
        "hotkeys": ["Ctrl+J", "Ctrl+L", "Ctrl+R", "Ctrl+W", "Escape", "Enter"],
    },
    "web": {
        "system_prompt": FRIDAY_SYSTEM_PROMPT,
        "voice_enabled": True,
        "wake_word_enabled": True,
        "system_control": False,
        "screen_capture": True,
        "always_listen": False,
        "tray_support": False,
        "websocket": True,
        "api_keys": True,
    },
    "mobile": {
        "system_prompt": EDITH_SYSTEM_PROMPT,
        "voice_enabled": True,
        "wake_word_enabled": True,
        "system_control": False,
        "screen_capture": False,
        "always_listen": False,
        "tray_support": False,
        "push_notifications": True,
        "background_sync": True,
        "offline_queue": True,
    },
}

# ── Committed latency / reliability targets (Phase SHOCK roadmap) ───────────────
# Concrete SLOs the voice-screen vertical is built against. Used by the perf
# monitor + VoiceScreenPanel to show live vs target. Not advisory — the boot/
# fallback/monitoring infrastructure is wired to meet these.
LATENCY_TARGETS: dict = {
    "wake_to_reply_sec": 2.0,  # wake word detected -> spoken reply begins
    "query_p95_sec": 3.0,  # user query -> first token/reply (p95)
    "provider_failover_ms": 100,  # FallbackChain.race stagger already 100ms
    "uptime_pct": 99.0,  # crash recovery + auto-restart target
}


def get_latency_targets() -> dict:
    return dict(LATENCY_TARGETS)


def get_platform_config(platform: str | None = None) -> dict:
    p = platform or PLATFORM
    return PLATFORM_CONFIGS.get(p, PLATFORM_CONFIGS["desktop"])
