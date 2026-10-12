# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Config - thin re-export of the canonical inner config.

Single source of truth: `orbit/orbit/config.py`.
This module exists only as a compatibility shim so that outer-level imports
(`from .config import X`) behave identically to the inner canonical config
and never diverge.
"""

from pathlib import Path

try:
    from dotenv import load_dotenv

    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

from orbit.orbit.config import *  # noqa: F401,F403
from orbit.orbit.config import (  # noqa: F401
    AVAILABLE_MODELS,
    DEFAULT_OLLAMA_MODEL,
    FIRECRAWL_API_KEY,
    FIRECRAWL_CACHE_TTL,
    FIRECRAWL_RATE_LIMIT_DELAY,
    FIRECRAWL_URL,
    FRONTEND_URL,
    GF_NAME,
    GIRLFRIEND_SYSTEM_PROMPT,
    GROQ_API_KEY,
    GROQ_API_URL,
    GROQ_MODELS,
    ORBIT_API_AUTH_ENABLED,
    ORBIT_API_KEY,
    ORBIT_API_PUBLIC_PATHS,
    ORBIT_SYSTEM_PROMPT,
    KOKORO_VOICE,
    LISTEN_PHRASE_LIMIT,
    LISTEN_TIMEOUT,
    MEM0_CONFIG,
    MEMORY_DIR,
    OFFLINE_MODE,
    OLLAMA_URL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    ASTRA_MODEL,
    ASTRA_REASONING_EFFORT,
    PORTKEY_API_KEY,
    PORTKEY_GATEWAY_URL,
    PORTKEY_VIRTUAL_KEY,
    PROVIDER_ROUTING,
    QDRANT_COLLECTION,
    QDRANT_URL,
    SAMPLE_RATE,
    TTS_ENGINE,
    TTS_SPEED,
    TTS_VOICE,
    TTS_VOICES,
    TITLE,
    TTS_VOICE_FEMALE,
    UNSTRUCTURED_API_KEY,
    UNSTRUCTURED_CHUNK_OVERLAP,
    UNSTRUCTURED_CHUNK_SIZE,
    UNSTRUCTURED_URL,
    VOSK_MODEL_DIR,
    VOSK_MODEL_NAME,
    VOSK_MODEL_URL,
    VOSK_MODEL_URL_ALPHA,
    XTTS_REFERENCE_DIR,
)
