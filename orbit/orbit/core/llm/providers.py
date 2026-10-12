# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
LLM Provider implementations for ORBIT Core
"""

import os
from abc import ABC, abstractmethod

import logging

from .router import LLMRequest, LLMResponse
from ..circuit_breaker import get_circuit_breaker, CircuitBreakerConfig, CircuitBreakerOpenError

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract base class for LLM providers"""

    @abstractmethod
    def __init__(self, api_key: str | None = None, **kwargs):
        pass

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse | None:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    def get_models(self) -> list[str]:
        pass


class OllamaProvider(LLMProvider):
    """Local Ollama provider"""

    def __init__(self, url: str = "http://127.0.0.1:11434", **kwargs):
        self.url = url
        self.api_key = None
        super().__init__(**kwargs)

    async def generate(self, request: LLMRequest) -> LLMResponse | None:
        from .router import get_router

        router = get_router()
        cb = get_circuit_breaker("ollama", CircuitBreakerConfig(failure_threshold=3, recovery_timeout=15.0))
        try:
            return await cb.call(router._call_ollama, request)
        except CircuitBreakerOpenError:
            logger.warning("Ollama circuit breaker open, skipping")
            return None

    def is_available(self) -> bool:
        return self._check_availability()

    def _check_availability(self) -> bool:
        try:
            import requests

            resp = requests.get(f"{self.url}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def get_models(self) -> list[str]:
        try:
            import requests

            resp = requests.get(f"{self.url}/api/tags", timeout=3)
            if resp.status_code == 200:
                return [m["name"] for m in resp.json().get("models", [])]
        except Exception:
            logger.exception("OllamaProvider failed to fetch available models")
            pass

        from ..config import AVAILABLE_MODELS

        return AVAILABLE_MODELS

    def pull_model(self, model_name: str) -> bool:
        try:
            import requests

            resp = requests.post(
                f"{self.url}/api/pull",
                json={"name": model_name},
                stream=True,
                timeout=30,
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def generate_offline(self, prompt: str, model: str = "qwen2.5:14b") -> str:
        request = LLMRequest(prompt=prompt, model=model, force_offline=True)
        from .router import get_router

        response = await get_router().generate(request)
        return response.content


class OpenRouterProvider(LLMProvider):
    """OpenRouter API provider"""

    def __init__(self, api_key: str | None = None, **kwargs):
        self.api_key = api_key
        self.base_url = os.environ.get("OPENROUTER_BASE_URL", "http://127.0.0.1:20128/v1")
        super().__init__(api_key, **kwargs)

    async def generate(self, request: LLMRequest) -> LLMResponse | None:
        from .router import get_router

        router = get_router()

        if not self.api_key or self.api_key == "your_openrouter_api_key_here":
            return None

        model = request.model or "google/gemini-2.0-flash-exp:free"
        cb = get_circuit_breaker("openrouter", CircuitBreakerConfig(failure_threshold=5, recovery_timeout=30.0))
        try:
            return await cb.call(router._call_openrouter, request, model)
        except CircuitBreakerOpenError:
            logger.warning("OpenRouter circuit breaker open, skipping")
            return None

    def is_available(self) -> bool:
        return self.api_key not in (None, "", "your_openrouter_api_key_here")

    def get_models(self) -> list[str]:
        return [
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.2-3b-instruct:free",
            "microsoft/phi-3-mini-128k-instruct:free",
            "mistralai/mistral-small-24b-instruct-2501:free",
            "anthropic/claude-3.5-sonnet",
            "openai/gpt-4o-mini",
        ]

    async def generate_with_routing(
        self,
        prompt: str,
        model: str = "google/gemini-2.0-flash-exp:free",
        system_prompt: str | None = None,
        history: list[dict] | None = None,
    ) -> str | None:
        request = LLMRequest(
            prompt=prompt,
            model=model,
            system_prompt=system_prompt,
            history=history or [],
        )
        response = await self.generate(request)
        return response.content if response else None


class OpenAIProvider(LLMProvider):
    """OpenAI direct provider (GPT-6 Astra first-class)."""

    def __init__(self, api_key: str | None = None, **kwargs):
        import os as _os

        self.api_key = api_key if api_key is not None else _os.environ.get("OPENAI_API_KEY", "")
        super().__init__(api_key, **kwargs)

    async def generate(self, request: LLMRequest) -> LLMResponse | None:
        from .router import get_router

        router = get_router()

        if not self.api_key:
            return None

        from ..config import ASTRA_MODEL

        model = request.model or ASTRA_MODEL
        cb = get_circuit_breaker("openai", CircuitBreakerConfig(failure_threshold=3, recovery_timeout=30.0))
        try:
            return await cb.call(router._call_openai, request, model)
        except CircuitBreakerOpenError:
            logger.warning("OpenAI circuit breaker open, skipping")
            return None

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_models(self) -> list[str]:
        from ..config import ASTRA_MODEL

        return [ASTRA_MODEL, "gpt-4o", "gpt-4o-mini"]


class GeminiProvider(LLMProvider):
    """Google Gemini API provider"""

    def __init__(self, api_key: str | None = None, **kwargs):
        self.api_key = api_key
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"
        super().__init__(api_key, **kwargs)

    async def generate(self, request: LLMRequest) -> LLMResponse | None:
        from .router import get_router

        router = get_router()

        if not self.api_key or self.api_key == "your_gemini_api_key_here":
            return None

        models = {
            "free": "gemini-2.0-flash-exp",
            "cheap": "gemini-2.0-flash-lite-preview-02-05",
            "best": "gemini-1.5-pro",
        }
        model = request.model or models.get("free")

        cb = get_circuit_breaker("gemini", CircuitBreakerConfig(failure_threshold=3, recovery_timeout=60.0))
        try:
            return await cb.call(router._call_gemini, request, model)
        except CircuitBreakerOpenError:
            logger.warning("Gemini circuit breaker open, skipping")
            return None

    def is_available(self) -> bool:
        return self.api_key not in (None, "", "your_gemini_api_key_here")

    def get_models(self) -> list[str]:
        return [
            "gemini-2.0-flash-exp",
            "gemini-2.0-flash-lite-preview-02-05",
            "gemini-1.5-pro",
        ]

    async def generate_with_flash(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history: list[dict] | None = None,
    ) -> str | None:
        request = LLMRequest(
            prompt=prompt,
            system_prompt=system_prompt,
            history=history or [],
        )
        response = await self.generate(request)
        return response.content if response else None


class PortkeyProvider(LLMProvider):
    """LLM provider routed through Portkey AI Gateway"""

    def __init__(self, api_key: str | None = None, gateway_url: str | None = None, **kwargs):
        self.api_key = api_key
        self.gateway_url = gateway_url or "http://localhost:20128/v1"
        self._client = None
        super().__init__(**kwargs)

    async def generate(self, request: LLMRequest) -> LLMResponse | None:
        from .router import get_router

        router = get_router()

        cb = get_circuit_breaker("portkey", CircuitBreakerConfig(failure_threshold=3, recovery_timeout=30.0))
        try:
            return await cb.call(router._call_portkey, request, self.gateway_url, self.api_key)
        except CircuitBreakerOpenError:
            logger.warning("Portkey circuit breaker open, skipping")
            return None

    def is_available(self) -> bool:
        try:
            import httpx

            r = httpx.get(f"{self.gateway_url.rstrip('/')}/models", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def get_models(self) -> list[str]:
        try:
            import httpx

            r = httpx.get(f"{self.gateway_url.rstrip('/')}/models", timeout=5)
            if r.status_code == 200:
                data = r.json()
                models = data if isinstance(data, list) else data.get("data", [])
                if models and isinstance(models[0], dict):
                    return [m.get("id", str(m)) for m in models]
                return [str(m) for m in models]
        except Exception:
            logger.exception("PortkeyProvider failed to fetch models")
            pass
        return [
            "gpt-4o-mini",
            "gpt-4o",
            "claude-sonnet-4-20250514",
            "claude-3-haiku-20240307",
            "gemini-2.0-flash-exp",
            "qwen/qwen3-coder",
            "deepseek/deepseek-chat",
        ]
