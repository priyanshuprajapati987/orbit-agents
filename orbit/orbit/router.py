# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT LLM Router Module

This module implements multi-provider LLM routing with intelligent fallback:

Provider Priority:
1. Google Gemini (if API key configured)
2. OpenRouter (if API key configured)
3. Ollama (local, always available)
4. Offline fallback (phi3 model)

Features:
- Provider health tracking (circuit-breaker pattern)
- Response caching with TTL (5 minutes)
- Tiered model selection (free/cheap/best)
- Offline fallback capability

Key Functions:
    generate(prompt, model, history) - Main generation function
    generate_offline(prompt, model) - Force offline mode
    check_internet() - Network connectivity check
    get_status() - Provider status information

Usage:
    from orbit.router import generate

    response = generate("What is the weather?")
    # Automatically routes to best available provider
"""

import hashlib
import socket
import threading
import time

import requests

from .config import (
    CLOUDFLARE_AI_URL,
    CLOUDFLARE_API_TOKEN,
    CLOUDFLARE_MODELS,
    DEFAULT_OLLAMA_MODEL,
    GROQ_API_KEY,
    GROQ_API_URL,
    GROQ_MODELS,
    ORBIT_SYSTEM_PROMPT,
    OLLAMA_URL,
    PROVIDER_ROUTING,
    decrypt_env,
)
from .logging_config import get_logger

logger = get_logger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL_TIERS = {
    "free": [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
    ],
    "cheap": [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-nano-30b-a3b:free",
    ],
    "best": [
        "anthropic/claude-3.5-sonnet",
        "openai/gpt-4o-mini",
        "google/gemini-2.5-flash",
    ],
}

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODELS = {
    "free": "gemini-2.5-flash",
    "cheap": "gemini-2.0-flash",
    "best": "gemini-2.5-pro",
}
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
OFFLINE_FALLBACK_MODEL = "phi3"
ROUTER_TIER = "free"

_online = None
_last_check = 0
_lock = threading.Lock()
_current_provider = "ollama"
_current_model = DEFAULT_OLLAMA_MODEL
_provider_stats = {
    "openrouter": {"ok": 0, "fail": 0, "avg_ms": 0},
    "ollama": {"ok": 0, "fail": 0, "avg_ms": 0},
    "gemini": {"ok": 0, "fail": 0, "avg_ms": 0},
    "groq": {"ok": 0, "fail": 0, "avg_ms": 0},
    "cloudflare": {"ok": 0, "fail": 0, "avg_ms": 0},
}
_response_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300  # 5 minutes
_cache_timestamps = {}


def _strip_timestamps(history):
    if not history:
        return history
    return [{k: v for k, v in m.items() if k != "timestamp"} for m in history]


def _load_api_key():
    return decrypt_env("OPENROUTER_API_KEY", "")


def _load_gemini_key():
    return decrypt_env("GEMINI_API_KEY", "")


VALID_PROVIDERS = {"openrouter", "ollama", "gemini", "groq", "cloudflare"}


def _track_provider(provider, elapsed_ms, success):
    if provider not in VALID_PROVIDERS:
        return
    with _lock:
        s = _provider_stats[provider]
        if success:
            s["ok"] += 1
            total = s["ok"] + s["fail"]
            s["avg_ms"] = (s["avg_ms"] * (total - 1) + elapsed_ms) / total if total > 1 else elapsed_ms
        else:
            s["fail"] += 1


def _cache_key(prompt, model, history, system_prompt, extra_context):
    """Generate cache key from request parameters"""
    key_data = f"{prompt}|{model}|{str(history)}|{system_prompt}|{extra_context}"
    return hashlib.sha256(key_data.encode()).hexdigest()[:32]


def _get_cached_response(key):
    """Get cached response if not expired"""
    with _cache_lock:
        if key in _response_cache:
            if key in _cache_timestamps:
                if time.time() - _cache_timestamps[key] < CACHE_TTL:
                    return _response_cache[key]
                else:
                    # Expired
                    del _response_cache[key]
                    del _cache_timestamps[key]
    return None


def _set_cached_response(key, response):
    """Cache response with timestamp"""
    with _cache_lock:
        _response_cache[key] = response
        _cache_timestamps[key] = time.time()


def _try_openrouter(prompt, model, history, system_prompt, extra_context):
    if not _healthy("openrouter"):
        return None

    api_key = _load_api_key()
    if not api_key or api_key == "your_openrouter_api_key_here":
        return None

    messages = []
    if system_prompt:
        sp = system_prompt
        if extra_context:
            sp += f"\n\n[Context]\n{extra_context}"
        messages.append({"role": "system", "content": sp})
    if history:
        messages.extend(_strip_timestamps(history))
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": "ORBIT Desktop",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
            },
            timeout=60,
        )
        elapsed = (time.time() - start) * 1000
        if resp.status_code == 200:
            _track_provider("openrouter", elapsed, True)
            logger.debug("OpenRouter request successful", extra={"model": model, "elapsed_ms": elapsed})
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content")
        _track_provider("openrouter", elapsed, False)
        return None
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        _track_provider("openrouter", elapsed, False)
        logger.error("OpenRouter request error", extra={"model": model, "error": str(e), "elapsed_ms": elapsed})
        return None


def _try_openrouter_tiered(prompt, history, system_prompt, extra_context):
    api_key = _load_api_key()
    if not api_key or api_key == "your_openrouter_api_key_here":
        return None

    tier = ROUTER_TIER
    models = OPENROUTER_MODEL_TIERS.get(tier, OPENROUTER_MODEL_TIERS["free"])
    errors = []
    for model in models:
        result = _try_openrouter(prompt, model, history, system_prompt, extra_context)
        if result:
            return result
        errors.append(model)
    return None


def _try_gemini(prompt, model, history, system_prompt, extra_context):
    api_key = _load_gemini_key()
    if not api_key or api_key == "your_gemini_api_key_here":
        return None

    contents = []
    if system_prompt:
        sp = system_prompt
        if extra_context:
            sp += f"\n\n[Context]\n{extra_context}"
        contents.append({"role": "user", "parts": [{"text": f"[System: {sp}]"}]})

    if history:
        for msg in history:
            role = "model" if msg.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})

    contents.append({"role": "user", "parts": [{"text": prompt}]})

    start = time.time()
    try:
        resp = requests.post(
            f"{GEMINI_API_URL}/{model}:generateContent",
            params={"key": api_key},
            json={"contents": contents, "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}},
            timeout=60,
        )
        elapsed = (time.time() - start) * 1000
        if resp.status_code == 200:
            candidates = resp.json().get("candidates", [])
            if candidates:
                text_parts = candidates[0].get("content", {}).get("parts", [])
                if text_parts:
                    _track_provider("gemini", elapsed, True)
                    logger.debug("Gemini request successful", extra={"model": model, "elapsed_ms": elapsed})
                    return text_parts[0].get("text", "")
        logger.warning(
            "Gemini request failed", extra={"model": model, "status": resp.status_code, "elapsed_ms": elapsed}
        )
        _track_provider("gemini", elapsed, False)
        return None
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        _track_provider("gemini", elapsed, False)
        logger.error("Gemini request error", extra={"model": model, "error": str(e), "elapsed_ms": elapsed})
        return None


def _try_groq(prompt, model, history, system_prompt, extra_context):
    api_key = GROQ_API_KEY
    if not api_key:
        return None

    messages = []
    if system_prompt:
        sp = system_prompt
        if extra_context:
            sp += f"\n\n[Context]\n{extra_context}"
        messages.append({"role": "system", "content": sp})
    if history:
        messages.extend(_strip_timestamps(history))
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
            },
            timeout=30,
        )
        elapsed = (time.time() - start) * 1000
        if resp.status_code == 200:
            _track_provider("groq", elapsed, True)
            logger.debug("Groq request successful", extra={"model": model, "elapsed_ms": elapsed})
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content")
        logger.warning("Groq request failed", extra={"model": model, "status": resp.status_code, "elapsed_ms": elapsed})
        _track_provider("groq", elapsed, False)
        return None
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        _track_provider("groq", elapsed, False)
        logger.error("Groq request error", extra={"model": model, "error": str(e), "elapsed_ms": elapsed})
        return None


def _try_cloudflare(prompt, model, history, system_prompt, extra_context):
    api_token = CLOUDFLARE_API_TOKEN
    if not api_token:
        return None

    messages = []
    if system_prompt:
        sp = system_prompt
        if extra_context:
            sp += f"\n\n[Context]\n{extra_context}"
        messages.append({"role": "system", "content": sp})
    if history:
        messages.extend(_strip_timestamps(history))
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    try:
        resp = requests.post(
            f"{CLOUDFLARE_AI_URL}/{model}",
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            json={"messages": messages},
            timeout=60,
        )
        elapsed = (time.time() - start) * 1000
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            response_text = result.get("response", "")
            if response_text:
                _track_provider("cloudflare", elapsed, True)
                logger.debug("Cloudflare Workers AI request successful", extra={"model": model, "elapsed_ms": elapsed})
                return response_text
        logger.warning(
            "Cloudflare request failed", extra={"model": model, "status": resp.status_code, "elapsed_ms": elapsed}
        )
        _track_provider("cloudflare", elapsed, False)
        return None
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        _track_provider("cloudflare", elapsed, False)
        logger.error("Cloudflare request error", extra={"model": model, "error": str(e), "elapsed_ms": elapsed})
        return None


def _try_provider_by_task(task_type, prompt, history, system_prompt, extra_context):
    """Route to the best provider for a given task type."""
    providers = PROVIDER_ROUTING.get(task_type, PROVIDER_ROUTING["general"])

    for provider in providers:
        if provider == "groq":
            groq_model = GROQ_MODELS.get(task_type, GROQ_MODELS["fast"])
            result = _try_groq(prompt, groq_model, history, system_prompt, extra_context)
            if result:
                return result, "groq", groq_model
        elif provider == "cloudflare":
            cf_model = CLOUDFLARE_MODELS.get(task_type, CLOUDFLARE_MODELS["general"])
            result = _try_cloudflare(prompt, cf_model, history, system_prompt, extra_context)
            if result:
                return result, "cloudflare", cf_model
        elif provider == "openrouter":
            result = _try_openrouter_tiered(prompt, history, system_prompt, extra_context)
            if result:
                return result, "openrouter", "tiered"
        elif provider == "gemini":
            gemini_model = GEMINI_MODELS.get(ROUTER_TIER, GEMINI_MODELS["free"])
            result = _try_gemini(prompt, gemini_model, history, system_prompt, extra_context)
            if result:
                return result, "gemini", gemini_model
        elif provider == "ollama":
            result = _call_ollama(prompt, DEFAULT_OLLAMA_MODEL, history, system_prompt, extra_context)
            if result and not result.startswith("[Error"):
                return result, "ollama", DEFAULT_OLLAMA_MODEL

    return None, None, None


def check_internet(timeout=2):
    global _online, _last_check
    now = time.time()
    if now - _last_check < 5 and _online is not None:
        return _online
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(("8.8.8.8", 53))
        _online = True
    except OSError:
        try:
            r = requests.get("https://www.google.com", timeout=timeout)
            _online = r.status_code == 200
        except Exception:
            _online = False
    _last_check = now
    return _online


def get_status():
    return {
        "online": _online if _online is not None else check_internet(),
        "provider": _current_provider,
        "model": _current_model,
        "tier": ROUTER_TIER,
        "ollama": _check_ollama_available(),
        "gemini_configured": _load_gemini_key() not in ("", "your_gemini_api_key_here"),
        "groq_configured": bool(GROQ_API_KEY),
        "cloudflare_configured": bool(CLOUDFLARE_API_TOKEN),
        "stats": dict(_provider_stats),
    }


def _healthy(provider):
    s = _provider_stats.get(provider, {"ok": 0, "fail": 0})
    total = s["ok"] + s["fail"]
    if total < 3:
        return True
    return s["ok"] / total > 0.3


def _check_ollama_available():
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def list_ollama_models():
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if resp.status_code == 200:
            return [m["name"] for m in resp.json().get("models", [])]
        return []
    except Exception:
        return []


def pull_ollama_model(model):
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/pull",
            json={"name": model},
            stream=True,
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _call_ollama(prompt, model, history, system_prompt, extra_context):
    if not _healthy("ollama") and not _check_ollama_available():
        logger.warning("Ollama unavailable", extra={"model": model})
        return "[Error: Ollama unavailable]"

    if system_prompt is None:
        system_prompt = ORBIT_SYSTEM_PROMPT
    if extra_context:
        system_prompt += f"\n\n[Context]\n{extra_context}"

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(_strip_timestamps(history))
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False, "options": {"temperature": 0.7}},
            timeout=120,
        )
        elapsed = (time.time() - start) * 1000
        if resp.status_code == 200:
            _track_provider("ollama", elapsed, True)
            logger.debug("Ollama request successful", extra={"model": model, "elapsed_ms": elapsed})
            return resp.json().get("message", {}).get("content")
        logger.warning(
            "Ollama request failed", extra={"model": model, "status": resp.status_code, "elapsed_ms": elapsed}
        )
        _track_provider("ollama", elapsed, False)
        return f"[Error: Ollama returned {resp.status_code}]"
    except requests.ConnectionError:
        _track_provider("ollama", 0, False)
        logger.error("Ollama connection error", extra={"model": model})
        return "[Error: Ollama is not running. Start it with 'ollama serve']"
    except Exception as e:
        _track_provider("ollama", 0, False)
        logger.error("Ollama request error", extra={"model": model, "error": str(e)})
        return f"[Error: {str(e)}]"


def generate(
    prompt, model=None, history=None, extra_context=None, system_prompt=None, force_offline=False, task_type=None
):
    global _current_provider, _current_model

    start_time = time.time()

    # Check cache first
    cache_key = _cache_key(prompt, model or "auto", history, system_prompt, extra_context)
    cached = _get_cached_response(cache_key)
    if cached:
        logger.debug("Cache hit", extra={"provider": "cache"})
        return cached

    # Experiment intercept
    _exp_id = None
    _var_id = None
    if task_type:
        try:
            from .core.experimentation_framework import get_experimentation_framework

            _new_prompt, _exp_id, _var_id = get_experimentation_framework().route_prompt(task_type, prompt)
            if _var_id:
                prompt = _new_prompt
                cache_key = _cache_key(prompt, model or "auto", history, system_prompt, extra_context)
        except Exception:
            logger.exception("Failed to route through experimentation framework")

    def _record_exp(success=True):
        if _var_id:
            try:
                from .core.experimentation_framework import get_experimentation_framework

                get_experimentation_framework().record_result(
                    _exp_id, _var_id, 0.5, (time.time() - start_time) * 1000, success
                )
            except Exception:
                logger.exception("Failed to record experiment result")

    online = check_internet() if not force_offline else False

    ollama_ok = _check_ollama_available()
    if not ollama_ok and not online:
        result = _call_ollama(prompt, OFFLINE_FALLBACK_MODEL, history, system_prompt, extra_context)
        latency_ms = (time.time() - start_time) * 1000
        logger.info(
            "LLM request: provider=ollama, model=%s, latency_ms=%.1f, success=True", OFFLINE_FALLBACK_MODEL, latency_ms
        )
        return result

    # Task-based routing: try providers in priority order for the task type
    if online and not force_offline and task_type:
        result, provider, used_model = _try_provider_by_task(task_type, prompt, history, system_prompt, extra_context)
        if result:
            _current_provider = provider
            _current_model = used_model
            _set_cached_response(cache_key, result)
            latency_ms = (time.time() - start_time) * 1000
            logger.info(
                "LLM request: provider=%s, model=%s, task=%s, latency_ms=%.1f, success=True",
                provider,
                used_model,
                task_type,
                latency_ms,
            )
            _record_exp()
            return result

    # Fallback: legacy priority order
    if online and not force_offline:
        gemini_key = _load_gemini_key()
        if gemini_key and gemini_key != "your_gemini_api_key_here":
            gemini_model = GEMINI_MODELS.get(ROUTER_TIER, GEMINI_MODELS["free"])
            result = _try_gemini(prompt, gemini_model, history, system_prompt, extra_context)
            if result:
                _current_provider = "gemini"
                _current_model = gemini_model
                _set_cached_response(cache_key, result)
                latency_ms = (time.time() - start_time) * 1000
                logger.info(
                    "LLM request: provider=gemini, model=%s, latency_ms=%.1f, success=True", gemini_model, latency_ms
                )
                _record_exp()
                return result

        result = _try_openrouter_tiered(prompt, history, system_prompt, extra_context)
        if result:
            _current_provider = "openrouter"
            _current_model = (
                _current_model
                if _current_model and _current_model != "tiered"
                else OPENROUTER_MODEL_TIERS.get(ROUTER_TIER, ["unknown"])[0]
            )
            _set_cached_response(cache_key, result)
            _record_exp()
            return result

    if ollama_ok:
        if model is None:
            model = DEFAULT_OLLAMA_MODEL
        _current_provider = "ollama"
        _current_model = model
        result = _call_ollama(prompt, model, history, system_prompt, extra_context)
        if result and not result.startswith("[Error"):
            _set_cached_response(cache_key, result)
        _record_exp()
        return result

    if online:
        gemini_key = _load_gemini_key()
        if gemini_key and gemini_key != "your_gemini_api_key_here":
            result = _try_gemini(prompt, DEFAULT_GEMINI_MODEL, history, system_prompt, extra_context)
            if result:
                _current_provider = "gemini"
                _current_model = DEFAULT_GEMINI_MODEL
                _set_cached_response(cache_key, result)
                _record_exp()
                return result

        result = _try_openrouter(prompt, DEFAULT_OPENROUTER_MODEL, history, system_prompt, extra_context)
        if result:
            _current_provider = "openrouter"
            _current_model = DEFAULT_OPENROUTER_MODEL
            _set_cached_response(cache_key, result)
            _record_exp()
            return result

    _current_provider = "ollama"
    _current_model = OFFLINE_FALLBACK_MODEL
    if not ollama_ok:
        _record_exp(False)
        return "[Error: All LLM providers unavailable — no online providers and Ollama is not running]"
    result = _call_ollama(prompt, OFFLINE_FALLBACK_MODEL, history, system_prompt, extra_context)
    if result and not result.startswith("[Error"):
        _set_cached_response(cache_key, result)
    _record_exp()
    return result


def generate_offline(prompt, model=None, history=None, extra_context=None, system_prompt=None):
    global _current_provider, _current_model
    if model is None:
        model = OFFLINE_FALLBACK_MODEL
    _current_provider = "ollama"
    _current_model = f"{model} (offline)"
    return _call_ollama(prompt, model, history, system_prompt, extra_context)


def set_tier(tier):
    global ROUTER_TIER
    if tier in OPENROUTER_MODEL_TIERS:
        ROUTER_TIER = tier


def get_tier_list():
    return list(OPENROUTER_MODEL_TIERS.keys())
