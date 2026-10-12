# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Legacy Router Adapter

Synchronous facade exposing the historical ``orbit.router.generate``
signature over the modern async ``core.llm.router`` pipeline. Callers keep
their existing call sites (same kwargs, same plain-string ``[Error: ...]``
convention) while routing, circuit breaking, caching, and category-aware
prompts come from the unified core/llm router.

Task type values follow the legacy ``PROVIDER_ROUTING`` keys
(coding/reasoning/fast/general/writing/search/creative/facts/vision/voice)
and are mapped onto modern ``QueryType`` categories. When the modern router
raises unexpectedly, the adapter falls back to the legacy router so agents
are never blocked during migration.
"""

import asyncio
import logging
import threading
import time

from ..config import OLLAMA_URL

from .router import LLMRequest, get_router

logger = logging.getLogger(__name__)

# Legacy PROVIDER_ROUTING task types -> modern QueryType categories.
TASK_TYPE_TO_CATEGORY = {
    "coding": "code",
    "reasoning": "reasoning",
    "fast": "fast",
    "general": "general",
    "writing": "creative",
    "search": "search",
    "creative": "creative",
    "facts": "facts",
    "vision": "general",
    "voice": "chat",
}

_current_provider = "unknown"
_current_model = None
_status_lock = threading.Lock()


def _strip_timestamps(history):
    if not history:
        return history
    return [{k: v for k, v in m.items() if k != "timestamp"} for m in history]


def _run_coro(coro):
    """Run an async coroutine from a synchronous context."""
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        if "running event loop" not in str(e):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _experiment_intercept(task_type, prompt):
    """Apply the legacy experimentation-framework prompt rewrite."""
    _exp_id = None
    _var_id = None
    if not task_type:
        return prompt, _exp_id, _var_id
    try:
        from ..experimentation_framework import get_experimentation_framework

        _new_prompt, _exp_id, _var_id = get_experimentation_framework().route_prompt(task_type, prompt)
        if _var_id:
            prompt = _new_prompt
    except Exception:
        logger.exception("Failed to route through experimentation framework")
    return prompt, _exp_id, _var_id


def _record_experiment(exp_id, var_id, latency_ms, success):
    if not var_id:
        return
    try:
        from ..experimentation_framework import get_experimentation_framework

        get_experimentation_framework().record_result(exp_id, var_id, 0.5, latency_ms, success)
    except Exception:
        logger.exception("Failed to record experiment result")


def generate(
    prompt, model=None, history=None, extra_context=None, system_prompt=None, force_offline=False, task_type=None
):
    """Generate a response, matching the legacy ``orbit.router.generate``
    signature. Returns plain text; failures are ``[Error: ...]`` strings.
    """
    global _current_provider, _current_model
    start_time = time.monotonic()

    # Safety gate (kill switch + trust) — enforced HERE as well as inside the
    # modern router so a blocked call can never slip through the legacy
    # fallback path (known bypass: router blocks -> success=False -> this
    # function would happily fall back to _legacy_generate and LLM anyway).
    try:
        from ..kill_switch import get_trust_manager

        _gate = get_trust_manager().can_act("ROUTER", cost_usd=0.0, tool="llm_generate")
    except Exception:
        _gate = {"allowed": True}
    if not _gate.get("allowed", True):
        _reason = _gate.get("reason", "blocked")
        logger.warning("LLM call blocked by safety gate: %s", _reason)
        return f"[Error: LLM call blocked ({_reason})]"

    prompt, exp_id, var_id = _experiment_intercept(task_type, prompt)

    category = TASK_TYPE_TO_CATEGORY.get(task_type)
    request = LLMRequest(
        prompt=prompt,
        model=model,
        history=_strip_timestamps(history),
        system_prompt=system_prompt,
        extra_context=extra_context,
        force_offline=force_offline,
        category=category,
    )

    try:
        response = _run_coro(get_router().generate(request))
    except Exception as e:
        logger.warning("Modern router raised %s; falling back to legacy router", e)
        return _legacy_generate(
            prompt, model, history, extra_context, system_prompt, force_offline, task_type, exp_id, var_id, start_time
        )

    with _status_lock:
        _current_provider = response.provider
        _current_model = response.model
    latency_ms = (time.monotonic() - start_time) * 1000
    _record_experiment(exp_id, var_id, latency_ms, response.success)
    if response.success:
        logger.info(
            "LLM request: provider=%s, model=%s, task=%s, latency_ms=%.1f, success=True",
            response.provider,
            response.model,
            task_type or category,
            latency_ms,
        )
        return response.content

    logger.warning(
        "LLM request failed: provider=%s, model=%s, error=%s, latency_ms=%.1f; falling back to legacy router",
        response.provider,
        response.model,
        response.error,
        latency_ms,
    )
    return _legacy_generate(
        prompt, model, history, extra_context, system_prompt, force_offline, task_type, exp_id, var_id, start_time
    )


def _safety_record(prompt, model, result, task_type, latency_ms, success):
    """Audit + trust usage + circuit-breaker record for a legacy-fallback call.

    The modern router logs all three via LLMRouter._audit_call; the legacy
    fallback path used to bypass them entirely (CONTEXT "Legacy Fallback
    Safety Gap"). Kill-switch + trust *checks* are already enforced at
    generate() entry — this records the CALL itself so per-day usage counts,
    cost/audit stats include fallback traffic, and breaker status shows
    fallback health. Record-only by design: never gates the fallback
    ("pipeline never blocked").
    """
    text = result if isinstance(result, str) else str(result or "")
    try:
        from ..llm_audit import get_llm_audit

        get_llm_audit().log(
            provider="legacy-fallback",
            model=model or "",
            input_tokens=len(prompt or "") // 4,
            output_tokens=len(text) // 4,
            cost_usd=0.0,
            latency_ms=int(latency_ms),
            success=success,
            error="" if success else text[:200],
            agent_id="ROUTER",
            task_type=task_type or "",
            category=TASK_TYPE_TO_CATEGORY.get(task_type) or "",
            prompt_preview=(prompt or "")[:100],
        )
    except Exception:
        logger.debug("Legacy fallback audit record failed", exc_info=True)
    try:
        from ..kill_switch import get_trust_manager

        get_trust_manager().record_usage("ROUTER", 0.0)
    except Exception:
        logger.debug("Legacy fallback trust record failed", exc_info=True)
    try:
        from ..circuit_breaker import get_circuit_breaker

        cb = get_circuit_breaker("legacy-fallback")
        if success:
            cb._on_success_sync()
        else:
            cb._on_failure_sync(Exception(text[:200] or "legacy router failure"))
    except Exception:
        logger.debug("Legacy fallback breaker record failed", exc_info=True)


def _legacy_generate(
    prompt, model, history, extra_context, system_prompt, force_offline, task_type, exp_id, var_id, start_time
):
    """Fallback path through the legacy router."""
    try:
        from ...router import generate as legacy_generate

        result = legacy_generate(
            prompt,
            model=model,
            history=history,
            extra_context=extra_context,
            system_prompt=system_prompt,
            force_offline=force_offline,
            task_type=task_type,
        )
    except Exception as e:
        logger.exception("Legacy router also failed: %s", e)
        result = f"[Error: {e}]"
    if result is None:
        result = "[Error: legacy router returned None]"
    latency_ms = (time.monotonic() - start_time) * 1000
    success = not result.startswith("[Error")
    _record_experiment(exp_id, var_id, latency_ms, success)
    _safety_record(prompt, model, result, task_type, latency_ms, success)
    return result


OFFLINE_FALLBACK_MODEL = "phi3"


def generate_offline(prompt, model=None, history=None, extra_context=None, system_prompt=None):
    """Force offline generation (Ollama only)."""
    global _current_provider, _current_model
    if model is None:
        model = OFFLINE_FALLBACK_MODEL
    with _status_lock:
        _current_provider = "ollama"
        _current_model = f"{model} (offline)"
    request = LLMRequest(
        prompt=prompt,
        model=model,
        history=_strip_timestamps(history),
        system_prompt=system_prompt,
        extra_context=extra_context,
        force_offline=True,
    )
    try:
        response = _run_coro(get_router().generate(request))
        if response.success:
            return response.content
        logger.warning("Modern offline generation failed (%s); falling back to legacy", response.error)
    except Exception as e:
        logger.warning("Modern offline generation raised (%s); falling back to legacy", e)
    try:
        from ...router import generate_offline as legacy_offline

        return legacy_offline(prompt, model, history, extra_context, system_prompt)
    except Exception as e2:
        logger.exception("Legacy offline generation failed: %s", e2)
        return f"[Error: {e2}]"


def check_internet(timeout=2):
    """Check internet connectivity (delegates to the modern router)."""
    return get_router()._check_internet(timeout)


def get_status():
    """Status dict shaped like the legacy router, sourced from the modern one."""
    import os

    status = get_router().get_status()
    with _status_lock:
        prov = _current_provider
        mdl = _current_model
    try:
        from ..provider_keys import get_provider_key

        openrouter_live = get_provider_key("openrouter")
    except Exception:
        openrouter_live = None
    return {
        "online": status.get("online", False),
        "provider": prov,
        "model": mdl,
        "tier": get_tier_list()[0] if get_tier_list() else "free",
        "ollama": _check_ollama_available(),
        "openrouter_configured": bool(openrouter_live or os.environ.get("OPENROUTER_API_KEY")),
        "gemini_configured": status.get("provider_health", {}).get("gemini") in ("available", "unknown"),
        "groq_configured": status.get("provider_health", {}).get("groq") in ("available", "unknown"),
        "cloudflare_configured": status.get("provider_health", {}).get("cloudflare") in ("available", "unknown"),
        "stats": status.get("provider_stats", {}),
    }


def _check_ollama_available():
    try:
        import requests

        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def list_ollama_models():
    """List models available in the local Ollama instance."""
    try:
        import requests

        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if resp.status_code == 200:
            return [m["name"] for m in resp.json().get("models", [])]
        return []
    except Exception:
        return []


def pull_ollama_model(model):
    """Pull a model into the local Ollama instance."""
    try:
        import requests

        resp = requests.post(
            f"{OLLAMA_URL}/api/pull",
            json={"name": model},
            stream=True,
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False


def set_tier(tier):
    """Delegate tier selection to the legacy router."""
    try:
        from ...router import set_tier as legacy_set_tier

        legacy_set_tier(tier)
    except Exception:
        logger.exception("Failed to set tier %s", tier)


def get_tier_list():
    """Available tier names (delegates to the legacy router)."""
    try:
        from ...router import get_tier_list as legacy_tiers

        return legacy_tiers()
    except Exception:
        return ["free"]


__all__ = [
    "generate",
    "generate_offline",
    "check_internet",
    "get_status",
    "list_ollama_models",
    "pull_ollama_model",
    "set_tier",
    "get_tier_list",
    "TASK_TYPE_TO_CATEGORY",
]
