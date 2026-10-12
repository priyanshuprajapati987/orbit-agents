# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT LLM Audit Logger

Tracks every LLM API call with provider, model, tokens, cost, latency,
and agent attribution. Append-only JSONL with secret redaction.

This gives full visibility into where money is being spent.
"""

import json
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from ..logging_config import get_logger
from .runtime_paths import orbit_data_dir

logger = get_logger(__name__)

_AUDIT_DIR = orbit_data_dir() / "llm_audit"
_AUDIT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class LLMAuditEntry:
    audit_id: str = ""
    ts: str = ""
    provider: str = ""
    model: str = ""
    agent_id: str = ""
    task_type: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    success: bool = True
    error: str = ""
    from_cache: bool = False
    category: str = ""
    prompt_preview: str = ""  # first 100 chars, redacted
    source: str = "system"  # system (router) vs manual (/api/cost/record)


class LLMAuditLogger:
    """Track every LLM API call for cost visibility and accountability."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._audit_file = _AUDIT_DIR / "llm_calls.jsonl"
        self._today_str = datetime.now().strftime("%Y%m%d")
        self._today_file = _AUDIT_DIR / f"llm_{self._today_str}.jsonl"
        self._stats: Dict[str, Dict] = defaultdict(
            lambda: {
                "calls": 0,
                "total_cost": 0.0,
                "total_tokens": 0,
                "errors": 0,
                "cache_hits": 0,
                "avg_latency_ms": 0,
            }
        )
        self._total_cost_today = 0.0
        self._total_calls_today = 0
        self._write_lock = threading.Lock()
        self._initialized = True

    def _check_midnight_rollover(self):
        """Reset daily counters and file when date changes."""
        today_str = datetime.now().strftime("%Y%m%d")
        if today_str != self._today_str:
            self._today_str = today_str
            self._today_file = _AUDIT_DIR / f"llm_{today_str}.jsonl"
            self._total_cost_today = 0.0
            self._total_calls_today = 0
            self._stats.clear()

    def log(
        self,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        success: bool = True,
        error: str = "",
        agent_id: str = "",
        task_type: str = "",
        from_cache: bool = False,
        category: str = "",
        prompt_preview: str = "",
    ) -> str:
        """Log an LLM API call. Returns the audit_id."""
        audit_id = uuid.uuid4().hex[:12]
        total_tokens = input_tokens + output_tokens

        entry = LLMAuditEntry(
            audit_id=audit_id,
            ts=datetime.now().isoformat(),
            provider=provider,
            model=model,
            agent_id=agent_id,
            task_type=task_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=round(cost_usd, 6),
            latency_ms=latency_ms,
            success=success,
            error=error,
            from_cache=from_cache,
            category=category,
            prompt_preview=prompt_preview[:100] if prompt_preview else "",
        )

        # Update in-memory stats (thread-safe)
        with self._write_lock:
            self._check_midnight_rollover()
            key = f"{provider}/{model}"
            self._stats[key]["calls"] += 1
            self._stats[key]["total_cost"] += cost_usd
            self._stats[key]["total_tokens"] += total_tokens
            if not success:
                self._stats[key]["errors"] += 1
            if from_cache:
                self._stats[key]["cache_hits"] += 1
            # Rolling average latency
            n = self._stats[key]["calls"]
            old_avg = self._stats[key]["avg_latency_ms"]
            self._stats[key]["avg_latency_ms"] = old_avg + (latency_ms - old_avg) / n

            self._total_cost_today += cost_usd
            self._total_calls_today += 1

        # Persist to JSONL (thread-safe)
        try:
            data = {
                "audit_id": audit_id,
                "ts": entry.ts,
                "provider": provider,
                "model": model,
                "agent_id": agent_id,
                "task_type": task_type,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": entry.cost_usd,
                "latency_ms": latency_ms,
                "success": success,
                "error": error,
                "from_cache": from_cache,
                "category": category,
                "prompt_preview": entry.prompt_preview,
            }
            try:
                from .secret_redactor import get_redactor

                data = get_redactor().redact_value(data)
            except Exception:
                pass
            line = json.dumps(data, ensure_ascii=False) + "\n"
            with self._write_lock:
                with open(self._audit_file, "a", encoding="utf-8") as f:
                    f.write(line)
                with open(self._today_file, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as e:
            logger.error("LLM audit write failed: %s", e)

        return audit_id

    def get_stats(self) -> Dict:
        return {
            "total_cost_today": round(self._total_cost_today, 4),
            "total_calls_today": self._total_calls_today,
            "by_provider_model": dict(self._stats),
            "audit_file": str(self._audit_file),
        }

    def get_recent(self, limit: int = 50) -> List[Dict]:
        entries = []
        try:
            with self._write_lock:
                if self._audit_file.exists():
                    with open(self._audit_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    entries.append(json.loads(line))
                                except json.JSONDecodeError:
                                    continue
        except Exception:
            pass
        return entries[-limit:]

    def get_cost_by_agent(self) -> Dict[str, float]:
        costs = defaultdict(float)
        try:
            with self._write_lock:
                if self._audit_file.exists():
                    with open(self._audit_file, "r", encoding="utf-8") as f:
                        for line in f:
                            try:
                                e = json.loads(line.strip())
                                aid = e.get("agent_id", "unknown")
                                costs[aid] += e.get("cost_usd", 0)
                            except json.JSONDecodeError:
                                continue
        except Exception:
            pass
        return dict(costs)

    def get_cost_by_provider(self) -> Dict[str, float]:
        costs = defaultdict(float)
        try:
            with self._write_lock:
                if self._audit_file.exists():
                    with open(self._audit_file, "r", encoding="utf-8") as f:
                        for line in f:
                            try:
                                e = json.loads(line.strip())
                                prov = e.get("provider", "unknown")
                                costs[prov] += e.get("cost_usd", 0)
                            except json.JSONDecodeError:
                                continue
        except Exception:
            pass
        return dict(costs)


def get_llm_audit() -> LLMAuditLogger:
    return LLMAuditLogger()
