# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Multi-Provider Orchestrator

Task-aware routing: classify the query, pick the best provider + model per
query type, then fail over through a ranked chain with circuit-breaker and
API-key awareness. Tuned to beat ChatGPT on latency and reasoning depth.

Routes:
- REASONING -> DeepSeek R1 (OpenRouter)  for deep thinking, math, logic
- FAST      -> Groq Llama 3.3 70B        for sub-second quick replies
- CREATIVE  -> Gemini Flash              for writing, ideas, tone
- CODE      -> OpenRouter coder          for code generation/fixing
- CHAT      -> Groq Llama (fast)         for casual back-and-forth
- SEARCH    -> Gemini Flash              for live lookups (news, weather)
- FACTS     -> Gemini Flash              for static factual answers
- GENERAL   -> Gemini / OpenRouter       default
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple

from ..config import (
    ASTRA_MODEL,
    CLOUDFLARE_API_TOKEN,
    CLOUDFLARE_MODELS,
    DEFAULT_OLLAMA_MODEL,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    GROQ_MODELS,
    OPENAI_API_KEY,
    OPENROUTER_API_KEY,
)
from ..circuit_breaker import get_circuit_breaker

logger = logging.getLogger(__name__)


class QueryType(Enum):
    REASONING = "reasoning"
    FAST = "fast"
    CREATIVE = "creative"
    CODE = "code"
    CHAT = "chat"
    SEARCH = "search"
    FACTS = "facts"
    GENERAL = "general"


@dataclass
class RoutePlan:
    query_type: QueryType
    complexity: str  # simple | complex
    confidence: float
    chain: List[Tuple[str, str]]  # [(provider, model), ...] in priority order
    skipped: List[Tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query_type": self.query_type.value,
            "complexity": self.complexity,
            "confidence": round(self.confidence, 2),
            "chain": [{"provider": p, "model": m} for p, m in self.chain],
            "skipped": [{"provider": p, "model": m} for p, m in self.skipped],
        }


# ── DeepSeek R1 via OpenRouter ────────────────────────────────────────────────
DEEPSEEK_R1_MODEL = "deepseek/deepseek-r1"
OPENROUTER_CODER = "qwen/qwen3-coder"
OPENROUTER_GENERAL = "openai/gpt-4o-mini"
# GPT-6 Astra direct (OpenAI) — frontier brain when OPENAI_API_KEY is set.
ASTRA_DIRECT = ASTRA_MODEL


class QueryClassifier:
    """Deterministic keyword + heuristic classifier. Fast, zero-LLM cost."""

    PHRASES = {
        QueryType.REASONING: [
            "explain in detail",
            "step by step",
            "think through",
            "what is the best",
            "compare and contrast",
            "pros and cons",
            "break down",
            "how does",
            "why does",
            "what causes",
            "analyze",
            "evaluate",
            "is it better",
            "solve",
            "calculate",
            "prove",
            "what's the difference",
            "logical",
        ],
        QueryType.FAST: [
            "quick",
            "short answer",
            "in one word",
            "tldr",
            "briefly",
            "just say",
            "yes or no",
            "y/n",
            "fast",
            "summarize in one sentence",
            "quickly",
        ],
        QueryType.CREATIVE: [
            "write a story",
            "write a poem",
            "creative",
            "brainstorm",
            "idea",
            "make it fun",
            "come up with",
            "imagine",
            "song",
            "joke",
            "poem",
            "story about",
            "title for",
            "motto",
            "slogan",
            "ad copy",
            "tagline",
            "design",
            "name ideas",
        ],
        QueryType.CODE: [
            "write code",
            "code to",
            "function",
            "class",
            "debug",
            "refactor",
            "python",
            "javascript",
            "typescript",
            "react",
            "flask",
            "api endpoint",
            "sql query",
            "regex",
            "bug in",
            "fix this",
            "compile",
            "algorithm",
            "implement",
            "git command",
        ],
        QueryType.SEARCH: [
            "what is the latest",
            "news about",
            "search for",
            "find out",
            "look up",
            "what's new",
            "who won",
            "weather in",
            "current time",
            "today in history",
        ],
        QueryType.FACTS: [
            "capital of",
            "population of",
            "when was",
            "who invented",
            "who discovered",
            "how many",
            "what is the largest",
            "what is the tallest",
            "what is the longest",
            "who wrote",
            "what year",
            "what is the distance",
            "who is the president",
            "what is the capital",
        ],
        QueryType.CHAT: [
            "how are you",
            "what's up",
            "hello",
            "hi",
            "hey",
            "good morning",
            "good night",
            "thank you",
            "thanks",
            "nice to meet",
            "who are you",
            "what can you do",
            "tell me about yourself",
        ],
    }

    SINGLE = {
        QueryType.REASONING: [
            "why",
            "how",
            "compare",
            "explain",
            "analyze",
            "evaluate",
            "difference",
            "logic",
            "reason",
            "math",
            "physics",
            "prove",
            "hypothesis",
            "derivative",
            "integral",
            "equation",
            "calculate",
            "solve",
            "theorem",
            "matrix",
            "probability",
            "algebra",
            "geometry",
        ],
        QueryType.FAST: ["quick", "brief", "short", "tldr", "summarize", "soon"],
        QueryType.CREATIVE: [
            "story",
            "poem",
            "song",
            "idea",
            "creative",
            "imagine",
            "joke",
            "essay",
            "brainstorm",
            "write",
            "haiku",
            "limerick",
            "rap",
            "tale",
            "narrative",
            "dialog",
            "scene",
            "sketch",
        ],
        QueryType.CODE: [
            "code",
            "debug",
            "function",
            "bug",
            "error",
            "python",
            "javascript",
            "html",
            "css",
            "sql",
            "api",
            "script",
            "refactor",
            "compile",
        ],
        QueryType.SEARCH: ["news", "search", "find", "latest", "weather", "price", "who", "what happened"],
        QueryType.FACTS: [
            "capital",
            "population",
            "invented",
            "discovered",
            "founded",
            "longest",
            "tallest",
            "largest",
            "who wrote",
            "history",
            "fact",
            "birthday",
            "famous",
        ],
        QueryType.CHAT: ["hello", "hi", "hey", "thanks", "how", "yourself"],
    }

    def classify(self, prompt: str) -> Tuple[QueryType, float]:
        text = prompt.lower().strip()

        if not text:
            return QueryType.GENERAL, 0.0

        scores: dict[QueryType, float] = {}
        for qtype, phrases in self.PHRASES.items():
            s = 0.0
            for phrase in phrases:
                if phrase in text:
                    s += 3.0 if len(phrase) > 8 else 2.0
            scores[qtype] = s

        for qtype, words in self.SINGLE.items():
            s = scores.get(qtype, 0.0)
            for w in words:
                if re.search(rf"\b{w}\b", text):
                    s += 1.0
            scores[qtype] = s

        # Length signal: long detailed prompts lean reasoning/creative, never fast
        if len(text) > 300:
            scores[QueryType.REASONING] = scores.get(QueryType.REASONING, 0) + 2.0
            scores[QueryType.CREATIVE] = scores.get(QueryType.CREATIVE, 0) + 1.0
            scores[QueryType.FAST] = 0.0
        elif len(text) < 25:
            scores[QueryType.FAST] = scores.get(QueryType.FAST, 0) + 1.0
            scores[QueryType.CHAT] = scores.get(QueryType.CHAT, 0) + 0.5

        best = max(scores.items(), key=lambda kv: kv[1]) if scores else (QueryType.GENERAL, 0.0)
        qtype, score = best

        total = sum(scores.values())
        confidence = (score / total) if total > 0 else 0.0

        if score == 0:
            return QueryType.GENERAL, 0.0
        return qtype, min(confidence, 0.95)

    def complexity(self, prompt: str) -> str:
        text = prompt.lower()
        depth_markers = sum(
            1
            for m in ["why", "explain", "compare", "analyze", "difference", "best way", "step by step", "in detail"]
            if m in text
        )
        if len(prompt) > 400 or depth_markers >= 2:
            return "complex"
        if len(prompt) > 120 or depth_markers >= 1:
            return "medium"
        return "simple"


class ProviderOrchestrator:
    ROUTES = {
        QueryType.REASONING: [
            ("openai", ASTRA_DIRECT),
            ("openrouter", DEEPSEEK_R1_MODEL),
            ("groq", GROQ_MODELS.get("reasoning", "llama-3.3-70b-versatile")),
            ("gemini", "gemini-2.0-flash-exp"),
            ("ollama", DEFAULT_OLLAMA_MODEL),
        ],
        QueryType.FAST: [
            ("groq", GROQ_MODELS.get("fast", "llama-3.3-70b-versatile")),
            ("gemini", "gemini-2.0-flash-exp"),
            ("ollama", DEFAULT_OLLAMA_MODEL),
        ],
        QueryType.CREATIVE: [
            ("gemini", "gemini-2.0-flash-exp"),
            ("openrouter", OPENROUTER_GENERAL),
            ("groq", GROQ_MODELS.get("fast", "llama-3.3-70b-versatile")),
            ("ollama", DEFAULT_OLLAMA_MODEL),
        ],
        QueryType.CODE: [
            ("openai", ASTRA_DIRECT),
            ("openrouter", OPENROUTER_CODER),
            ("groq", GROQ_MODELS.get("coding", "deepseek-r1-distill-llama-70b")),
            ("cloudflare", CLOUDFLARE_MODELS.get("coding", "@cf/deepseek-ai/deepseek-coder-6.7b-instruct-awq")),
            ("gemini", "gemini-2.0-flash-exp"),
            ("ollama", DEFAULT_OLLAMA_MODEL),
        ],
        QueryType.CHAT: [
            ("groq", GROQ_MODELS.get("fast", "llama-3.3-70b-versatile")),
            ("gemini", "gemini-2.0-flash-exp"),
            ("ollama", DEFAULT_OLLAMA_MODEL),
        ],
        QueryType.SEARCH: [
            ("gemini", "gemini-2.0-flash-exp"),
            ("openrouter", OPENROUTER_GENERAL),
            ("groq", GROQ_MODELS.get("fast", "llama-3.3-70b-versatile")),
            ("ollama", DEFAULT_OLLAMA_MODEL),
        ],
        QueryType.FACTS: [
            ("gemini", "gemini-2.0-flash-exp"),
            ("openrouter", OPENROUTER_GENERAL),
            ("groq", GROQ_MODELS.get("fast", "llama-3.3-70b-versatile")),
            ("ollama", DEFAULT_OLLAMA_MODEL),
        ],
        QueryType.GENERAL: [
            ("openai", ASTRA_DIRECT),
            ("gemini", "gemini-2.0-flash-exp"),
            ("openrouter", OPENROUTER_GENERAL),
            ("groq", GROQ_MODELS.get("fast", "llama-3.3-70b-versatile")),
            ("ollama", DEFAULT_OLLAMA_MODEL),
        ],
    }

    KEY_GATES = {
        "openai": (OPENAI_API_KEY, None),
        "openrouter": (OPENROUTER_API_KEY, "your_openrouter_api_key_here"),
        "groq": (GROQ_API_KEY, None),
        "gemini": (GEMINI_API_KEY, "your_gemini_api_key_here"),
        "cloudflare": (CLOUDFLARE_API_TOKEN, None),
    }

    def __init__(self):
        self.classifier = QueryClassifier()
        self.decisions: List[dict] = []
        self._quality_scores: dict[str, float] = {}  # provider -> 0.0-1.0 quality
        self._lock = threading.Lock()

    def record_quality(self, provider: str, quality: float):
        """Record response quality for a provider (0.0 = bad, 1.0 = great)."""
        with self._lock:
            current = self._quality_scores.get(provider, 0.5)
            # Exponential moving average
            self._quality_scores[provider] = current * 0.7 + quality * 0.3

    def _provider_available(self, provider: str) -> bool:
        # Vault-aware: a BYOK key saved at runtime (UI/API) must flip
        # availability without restart. Falls back to import-time env.
        try:
            from ..provider_keys import get_provider_key

            live = get_provider_key(provider)
        except Exception:
            live = None
        if live:
            return True
        key, placeholder = self.KEY_GATES.get(provider, (None, None))
        if key is not None:
            if not key or (placeholder and key == placeholder):
                return False
        return True

    def plan(self, prompt: str, offline: bool = False) -> RoutePlan:
        qtype, confidence = self.classifier.classify(prompt)
        complexity = self.classifier.complexity(prompt)

        if offline:
            chain = [("ollama", DEFAULT_OLLAMA_MODEL)]
            return RoutePlan(qtype, complexity, confidence, chain, [])

        candidates: List[Tuple[str, str]] = []
        skipped: List[Tuple[str, str]] = []
        for provider, model in self.ROUTES.get(qtype, self.ROUTES[QueryType.GENERAL]):
            if not self._provider_available(provider):
                skipped.append((provider, model))
                continue
            cb = get_circuit_breaker(provider)
            if cb.state.value == "open":
                skipped.append((provider, model))
                continue
            candidates.append((provider, model))

        # Rank candidates by quality score — lower quality providers get a penalty
        # so they sort later in the chain (first = best).
        def _rank(pair: Tuple[str, str]) -> float:
            p = pair[0]
            quality = self._quality_scores.get(p, 0.5)
            return quality  # higher = better → sorts first

        candidates.sort(key=_rank, reverse=True)
        chain = candidates

        if not chain:
            chain = [("ollama", DEFAULT_OLLAMA_MODEL)]

        # Gateway fallback: if no openrouter route in the chain and we're
        # online, append it last so the local OmniRouter gateway (auto/*)
        # can always serve — pipeline never blocked.
        if not offline and not any(p == "openrouter" for p, _ in chain):
            chain.append(("openrouter", OPENROUTER_GENERAL))

        plan = RoutePlan(qtype, complexity, confidence, chain, skipped)
        with self._lock:
            self.decisions.append(plan.to_dict())
            if len(self.decisions) > 200:
                self.decisions = self.decisions[-200:]
        logger.debug(
            "Route plan: type=%s complexity=%s confidence=%.2f chain=%s skipped=%s",
            qtype.value,
            complexity,
            confidence,
            [(p, m.split("/")[-1]) for p, m in chain],
            [p for p, _ in skipped],
        )
        return plan

    def get_stats(self) -> dict:
        type_counts: dict[str, int] = {}
        for d in self.decisions:
            t = d["query_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        return {
            "total_routes": len(self.decisions),
            "by_type": type_counts,
            "recent": self.decisions[-10:],
        }


_orchestrator: ProviderOrchestrator | None = None
_orchestrator_lock = threading.Lock()


def get_orchestrator() -> ProviderOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        with _orchestrator_lock:
            if _orchestrator is None:
                _orchestrator = ProviderOrchestrator()
    return _orchestrator


def plan_route(prompt: str, offline: bool = False) -> RoutePlan:
    return get_orchestrator().plan(prompt, offline=offline)


def get_orchestrator_stats() -> dict:
    return get_orchestrator().get_stats()
