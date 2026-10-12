# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""Cost Optimization Engine — minimize API costs while maintaining quality.

Features:
- Prompt optimization (shorten without losing meaning)
- Model selection (cheapest suitable for task)
- Cost tracking per call/project
- Cache hit optimization
- Budget enforcement
"""

import json
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .env_utils import float_env

logger = logging.getLogger(__name__)

def _data_dir():
    from .runtime_paths import orbit_data_dir
    d = orbit_data_dir() / "cost_optimizer"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _get_data_dir():
    return _data_dir()


# ---------------------------------------------------------------------------
# Model pricing (per 1K tokens, input/output)
# ---------------------------------------------------------------------------

MODEL_PRICING = {
    # Free tier
    "qwen2.5:14b": {"input": 0, "output": 0, "provider": "ollama"},
    "qwen2.5-coder:3b": {"input": 0, "output": 0, "provider": "ollama"},
    "llama3.2:1b": {"input": 0, "output": 0, "provider": "ollama"},
    "qwen3:8b": {"input": 0, "output": 0, "provider": "ollama"},
    "qwen3:1.7b": {"input": 0, "output": 0, "provider": "ollama"},
    "deepseek-r1:14b": {"input": 0, "output": 0, "provider": "ollama"},
    # Cheap
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006, "provider": "openai"},
    "gemini-2.0-flash": {"input": 0.000075, "output": 0.0003, "provider": "gemini"},
    "groq-llama-3.3-70b": {"input": 0.00059, "output": 0.00079, "provider": "groq"},
    # Medium
    "gpt-4o": {"input": 0.0025, "output": 0.01, "provider": "openai"},
    "claude-3.5-sonnet": {"input": 0.003, "output": 0.015, "provider": "anthropic"},
    "gemini-2.5-pro": {"input": 0.00125, "output": 0.01, "provider": "gemini"},
    # Expensive (best quality)
    "claude-3-opus": {"input": 0.015, "output": 0.075, "provider": "anthropic"},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03, "provider": "openai"},
    "o1": {"input": 0.015, "output": 0.06, "provider": "openai"},
}

# Per-provider pricing view consumed by the LLM router's audit cost estimate
# (router computes cost = est_tokens * output / 1_000_000, so values are
# USD per 1M tokens — MODEL_PRICING is per 1k, hence the x1000 scale).
PROVIDER_PRICING: dict = {}
for _model, _p in MODEL_PRICING.items():
    _prov = _p.get("provider", "unknown")
    _out_per_1m = float(_p.get("output", 0)) * 1000.0
    PROVIDER_PRICING.setdefault(_prov, {"default": {"output": _out_per_1m}})
    PROVIDER_PRICING[_prov][_model] = {"output": _out_per_1m}
# Local / self-hosted providers default to zero cost when a model is unknown.
for _prov in ("ollama", "portkey"):
    PROVIDER_PRICING.setdefault(_prov, {"default": {"output": 0.0}})
del _model, _p, _prov, _out_per_1m

# Task → recommended model tier
TASK_MODEL_MAP = {
    "fast": ["qwen2.5:14b", "gpt-4o-mini", "gemini-2.0-flash"],
    "chat": ["qwen2.5:14b", "gpt-4o-mini", "groq-llama-3.3-70b"],
    "code": ["qwen2.5-coder:3b", "gpt-4o-mini", "claude-3.5-sonnet"],
    "reasoning": ["qwen2.5:14b", "groq-llama-3.3-70b", "claude-3.5-sonnet"],
    "creative": ["gemini-2.0-flash", "gpt-4o-mini", "claude-3.5-sonnet"],
    "research": ["gpt-4o-mini", "gemini-2.0-flash", "gpt-4o"],
    "facts": ["gemini-2.0-flash", "gpt-4o-mini", "qwen2.5:14b"],
}


@dataclass
class CostEntry:
    timestamp: float
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    task_type: str = ""
    prompt_length: int = 0
    optimized: bool = False
    savings_usd: float = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class PromptOptimization:
    original_tokens: int
    optimized_tokens: int
    reduction_pct: float
    savings_usd: float
    optimized_prompt: str

    def to_dict(self):
        return asdict(self)


class CostOptimizer:
    """Minimize API costs while maintaining quality."""

    def __init__(self):
        self._lock = threading.RLock()
        self._costs: deque = deque(maxlen=5000)
        self._cache_hits: int = 0
        self._total_saved: float = 0
        self._budget_limit: float = float_env("ORBIT_DAILY_BUDGET", 10.0)
        self._today_cost: float = 0
        self._today = time.strftime("%Y-%m-%d")
        self._load_data()

    # --- Prompt Optimization ---

    def optimize_prompt(self, prompt: str, task_type: str = "general") -> dict:
        """Shorten prompt without losing meaning. Returns optimized prompt + stats."""
        original_tokens = self._estimate_tokens(prompt)

        # Strategy 1: Remove filler words
        optimized = self._remove_fillers(prompt)

        # Strategy 2: Compress based on task type
        if task_type == "code":
            optimized = self._compress_code_prompt(optimized)
        elif task_type == "research":
            optimized = self._compress_research_prompt(optimized)
        elif task_type == "creative":
            optimized = self._compress_creative_prompt(optimized)

        # Strategy 3: Remove redundant instructions
        optimized = self._remove_redundancy(optimized)

        optimized_tokens = self._estimate_tokens(optimized)
        reduction = (1 - optimized_tokens / max(original_tokens, 1)) * 100

        # Cost savings
        pricing = MODEL_PRICING.get("gpt-4o-mini", {"input": 0.00015})
        savings = ((original_tokens - optimized_tokens) / 1000) * pricing["input"]

        result = PromptOptimization(
            original_tokens=original_tokens,
            optimized_tokens=optimized_tokens,
            reduction_pct=round(reduction, 1),
            savings_usd=round(savings, 6),
            optimized_prompt=optimized,
        )

        with self._lock:
            self._total_saved += savings

        return result.to_dict()

    # --- Model Selection ---

    def select_model(self, task_type: str, quality_needed: str = "medium", max_cost_per_call: float = 0.05) -> dict:
        """Select cheapest suitable model for task."""
        candidates = TASK_MODEL_MAP.get(task_type, TASK_MODEL_MAP["fast"])

        # Filter by budget
        suitable = []
        for model in candidates:
            pricing = MODEL_PRICING.get(model, {})
            cost_per_1k = pricing.get("input", 0) + pricing.get("output", 0)
            if cost_per_1k <= max_cost_per_call * 1000:  # Per 1K tokens
                suitable.append(
                    {
                        "model": model,
                        "provider": pricing.get("provider", "unknown"),
                        "cost_per_1k_in": pricing.get("input", 0),
                        "cost_per_1k_out": pricing.get("output", 0),
                        "is_free": pricing.get("input", 1) == 0,
                    }
                )

        if not suitable:
            # Fallback to free tier
            suitable = [
                {
                    "model": c,
                    "provider": MODEL_PRICING.get(c, {}).get("provider", "unknown"),
                    "cost_per_1k_in": MODEL_PRICING.get(c, {}).get("input", 0),
                    "cost_per_1k_out": MODEL_PRICING.get(c, {}).get("output", 0),
                    "is_free": True,
                }
                for c in candidates
                if MODEL_PRICING.get(c, {}).get("input", 1) == 0
            ]

        if not suitable:
            suitable = [
                {
                    "model": candidates[0],
                    "provider": "unknown",
                    "cost_per_1k_in": 0,
                    "cost_per_1k_out": 0,
                    "is_free": True,
                }
            ]

        # Pick cheapest
        best = min(suitable, key=lambda x: x["cost_per_1k_in"] + x["cost_per_1k_out"])

        return {
            "selected": best["model"],
            "provider": best["provider"],
            "is_free": best["is_free"],
            "estimated_cost_per_1k": best["cost_per_1k_in"] + best["cost_per_1k_out"],
            "alternatives": [s["model"] for s in suitable if s["model"] != best["model"]][:3],
        }

    def estimate_cost(self, model: str, tokens_in: int, tokens_out: int) -> dict:
        """Estimate cost for a call."""
        pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0})
        cost_in = (tokens_in / 1000) * pricing["input"]
        cost_out = (tokens_out / 1000) * pricing["output"]
        total = cost_in + cost_out

        return {
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_in": round(cost_in, 6),
            "cost_out": round(cost_out, 6),
            "total_usd": round(total, 6),
            "is_free": total == 0,
        }

    # --- Cost Recording ---

    def record_call(
        self, model: str, tokens_in: int, tokens_out: int, task_type: str = "", prompt_length: int = 0
    ) -> dict:
        """Record an API call for cost tracking."""
        pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0, "provider": "unknown"})
        cost = (tokens_in / 1000) * pricing["input"] + (tokens_out / 1000) * pricing["output"]

        entry = CostEntry(
            timestamp=time.time(),
            model=model,
            provider=pricing.get("provider", "unknown"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=round(cost, 6),
            task_type=task_type,
            prompt_length=prompt_length,
        )

        with self._lock:
            self._costs.append(entry)
            # Reset daily counter
            today = time.strftime("%Y-%m-%d")
            if today != self._today:
                self._today = today
                self._today_cost = 0
            self._today_cost += cost
            self._save_data()

        # Budget alert
        budget_ok = self._today_cost < self._budget_limit
        if not budget_ok:
            logger.warning("DAILY BUDGET EXCEEDED: $%.2f / $%.2f", self._today_cost, self._budget_limit)

        return {
            "cost_usd": round(cost, 6),
            "daily_total": round(self._today_cost, 4),
            "budget_limit": self._budget_limit,
            "budget_ok": budget_ok,
            "cumulative_saved": round(self._total_saved, 4),
        }

    def record_cache_hit(self, tokens: int):
        """Record a cache hit (free call)."""
        pricing = MODEL_PRICING.get("gpt-4o-mini", {"input": 0.00015})
        saved = (tokens / 1000) * pricing["input"]
        with self._lock:
            self._cache_hits += 1
            self._total_saved += saved

    # --- Budget ---

    def set_budget(self, daily_limit: float):
        """Set daily budget limit."""
        self._budget_limit = daily_limit
        return {"daily_limit": daily_limit}

    def get_budget_status(self) -> dict:
        """Current budget status."""
        with self._lock:
            return {
                "daily_limit": self._budget_limit,
                "today_spent": round(self._today_cost, 4),
                "remaining": round(max(0, self._budget_limit - self._today_cost), 4),
                "pct_used": round((self._today_cost / self._budget_limit) * 100, 1),
                "budget_ok": self._today_cost < self._budget_limit,
            }

    # --- Stats ---

    def get_stats(self) -> dict:
        """Full stats for API."""
        with self._lock:
            costs = list(self._costs)

            total_cost = sum(c.cost_usd for c in costs)
            total_tokens = sum(c.tokens_in + c.tokens_out for c in costs)
            free_calls = sum(1 for c in costs if c.cost_usd == 0)
            avg_cost = total_cost / max(len(costs), 1)

            # By model
            by_model = {}
            for c in costs:
                if c.model not in by_model:
                    by_model[c.model] = {"calls": 0, "cost": 0, "tokens": 0}
                by_model[c.model]["calls"] += 1
                by_model[c.model]["cost"] += c.cost_usd
                by_model[c.model]["tokens"] += c.tokens_in + c.tokens_out

            # By task type
            by_task = {}
            for c in costs:
                if c.task_type:
                    if c.task_type not in by_task:
                        by_task[c.task_type] = {"calls": 0, "cost": 0}
                    by_task[c.task_type]["calls"] += 1
                    by_task[c.task_type]["cost"] += c.cost_usd

            return {
                "total_calls": len(costs),
                "total_cost_usd": round(total_cost, 4),
                "total_tokens": total_tokens,
                "today_cost": round(self._today_cost, 4),
                "avg_cost_per_call": round(avg_cost, 6),
                "free_calls": free_calls,
                "cache_hits": self._cache_hits,
                "total_saved": round(self._total_saved, 4),
                "budget": self.get_budget_status(),
                "by_model": {
                    k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()}
                    for k, v in by_model.items()
                },
                "by_task": {
                    k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()}
                    for k, v in by_task.items()
                },
            }

    def get_recent(self, limit: int = 20) -> list:
        """Recent cost entries."""
        with self._lock:
            return [c.to_dict() for c in list(self._costs)[-limit:]]

    def get_recommendations(self) -> list:
        """Cost optimization recommendations."""
        recommendations = []
        stats = self.get_stats()

        # Check if using expensive models for simple tasks
        for model, data in stats.get("by_model", {}).items():
            pricing = MODEL_PRICING.get(model, {})
            if pricing.get("input", 0) > 0.005 and data["calls"] > 10:
                recommendations.append(
                    {
                        "type": "model_downgrade",
                        "current": model,
                        "suggestion": "Use gpt-4o-mini for simple tasks",
                        "potential_savings": f"${data['cost'] * 0.8:.2f}",
                    }
                )

        # Check cache hit rate
        total = stats.get("total_calls", 0)
        hits = stats.get("cache_hits", 0)
        if total > 20 and hits / total < 0.2:
            recommendations.append(
                {
                    "type": "cache_improvement",
                    "current": f"{hits}/{total} hits",
                    "suggestion": "Increase cache TTL or expand cache key coverage",
                    "potential_savings": "30-50% on repeated queries",
                }
            )

        # Check budget usage
        budget = stats.get("budget", {})
        if budget.get("pct_used", 0) > 80:
            recommendations.append(
                {
                    "type": "budget_warning",
                    "current": f"{budget['pct_used']}% used",
                    "suggestion": "Switch to cheaper models or reduce call frequency",
                    "potential_savings": "Depends on usage pattern",
                }
            )

        return recommendations

    # --- Internal: Prompt Compression ---

    def _remove_fillers(self, prompt: str) -> str:
        """Remove filler words and phrases."""
        fillers = [
            (r"\bplease\b", ""),
            (r"\bkindly\b", ""),
            (r"\bcould you\b", ""),
            (r"\bwould you\b", ""),
            (r"\bi would like you to\b", ""),
            (r"\bi want you to\b", ""),
            (r"\bcan you\b", ""),
            (r"\bhelp me\b", ""),
            (r"\bI need you to\b", ""),
            (r"\bmake sure to\b", ""),
            (r"\bensure that\b", ""),
            (r"\bit is important to\b", ""),
            (r"\byou should\b", ""),
            (r"\bI want\b", ""),
            (r"\bthe following\b", ""),
            (r"\bas follows\b", ""),
        ]
        result = prompt
        for pattern, replacement in fillers:
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        # Clean extra whitespace
        result = re.sub(r"\s+", " ", result).strip()
        return result

    def _compress_code_prompt(self, prompt: str) -> str:
        """Compress code-related prompts."""
        # Remove verbose code instructions
        result = prompt
        result = re.sub(r"(?i)write (clean|good|proper|well-structured) code", "write code", result)
        result = re.sub(r"(?i)make sure the code is.*?[\.\n]", "", result)
        result = re.sub(r"(?i)follow best practices[\.\n]*", "", result)
        result = re.sub(r"(?i)include comments[\.\n]*", "", result)
        return result.strip()

    def _compress_research_prompt(self, prompt: str) -> str:
        """Compress research prompts."""
        result = prompt
        result = re.sub(r"(?i)provide a detailed comprehensive", "give", result)
        result = re.sub(r"(?i)in-depth analysis", "analysis", result)
        result = re.sub(r"(?i)make sure to cover all aspects[\.\n]*", "", result)
        return result.strip()

    def _compress_creative_prompt(self, prompt: str) -> str:
        """Compress creative prompts."""
        result = prompt
        result = re.sub(r"(?i)be creative and[\.\s]*", "", result)
        result = re.sub(r"(?i)use your imagination[\.\s]*", "", result)
        return result.strip()

    def _remove_redundancy(self, prompt: str) -> str:
        """Remove redundant sentences."""
        sentences = re.split(r"[.!?\n]+", prompt)
        seen = set()
        unique = []
        for s in sentences:
            s = s.strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                unique.append(s)
        return ". ".join(unique)

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (~4 chars per token)."""
        return max(len(text) // 4, 1)

    # --- Persistence ---

    def _save_data(self):
        """Persist cost data."""
        try:
            path = _get_data_dir() / "costs.json"
            data = [c.to_dict() for c in list(self._costs)[-1000:]]
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            # Save stats
            stats_path = _get_data_dir() / "stats.json"
            stats = {
                "total_saved": self._total_saved,
                "cache_hits": self._cache_hits,
                "today_cost": self._today_cost,
                "budget_limit": self._budget_limit,
                "last_update": time.time(),
            }
            stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("Failed to save cost data: %s", e)

    def _load_data(self):
        """Load cost data."""
        try:
            path = _get_data_dir() / "costs.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                for entry in data:
                    self._costs.append(
                        CostEntry(**{k: v for k, v in entry.items() if k in CostEntry.__dataclass_fields__})
                    )

            stats_path = _get_data_dir() / "stats.json"
            if stats_path.exists():
                stats = json.loads(stats_path.read_text(encoding="utf-8"))
                self._total_saved = stats.get("total_saved", 0)
                self._cache_hits = stats.get("cache_hits", 0)
                self._budget_limit = stats.get("budget_limit", 10.0)
                # Reset daily if stale
                if time.strftime("%Y-%m-%d") != time.strftime("%Y-%m-%d", time.localtime(stats.get("last_update", 0))):
                    self._today_cost = 0
                else:
                    self._today_cost = stats.get("today_cost", 0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: Optional[CostOptimizer] = None
_instance_lock = threading.Lock()


def get_cost_optimizer() -> CostOptimizer:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = CostOptimizer()
    return _instance
