# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Health Check System — Production Monitoring

Per-agent health tracking, token cost logging, error rates, and dashboard.
Patterns inspired by NirDiamant/agents-towards-production (LangSmith tracing + IntellAgent metrics).
"""

from .runtime_paths import data_dir
import json
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Callable
from pathlib import Path
from ..logging_config import get_logger

logger = get_logger(__name__)

# ── Cost constants (per 1K tokens, USD) ─────────────────────────────────────
MODEL_COST_PER_1K = {
    "ollama": 0.0,
    "groq": 0.0002,
    "openrouter": 0.003,
    "gemini": 0.000125,
    "cloudflare": 0.0001,
}


# ── Status constants ────────────────────────────────────────────────────────
class HealthStatus:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


# ── Structured telemetry events ─────────────────────────────────────────────
@dataclass
class AgentEvent:
    agent_label: str
    agent_name: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    success: bool = True
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    provider: str = "ollama"
    error_type: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class CostBudget:
    daily_limit_usd: float = 10.0
    per_request_limit_usd: float = 0.50
    _spent_today: float = field(default=0.0, repr=False)
    _day_start: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"), repr=False)
    _lock: object = field(default_factory=threading.Lock, repr=False)

    def can_spend(self, amount: float) -> bool:
        with self._lock:
            self._reset_if_new_day()
            return (self._spent_today + amount) <= self.daily_limit_usd and amount <= self.per_request_limit_usd

    def try_spend(self, amount: float) -> bool:
        with self._lock:
            self._reset_if_new_day()
            if (self._spent_today + amount) <= self.daily_limit_usd and amount <= self.per_request_limit_usd:
                self._spent_today += amount
                return True
            return False

    def record_spend(self, amount: float):
        with self._lock:
            self._reset_if_new_day()
            self._spent_today += amount

    def remaining(self) -> float:
        with self._lock:
            self._reset_if_new_day()
            return max(0.0, self.daily_limit_usd - self._spent_today)

    def _reset_if_new_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._day_start:
            self._spent_today = 0.0
            self._day_start = today


# ── Per-agent metrics store ─────────────────────────────────────────────────
class AgentMetricsStore:
    def __init__(self, max_history: int = 500):
        self.max_history = max_history
        self._lock = threading.Lock()
        self._events: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self._totals: Dict[str, Dict] = {}
        self._recent_errors: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))

    def record(self, event: AgentEvent):
        with self._lock:
            self._events[event.agent_label].append(event)

            if event.agent_label not in self._totals:
                self._totals[event.agent_label] = {
                    "total_calls": 0,
                    "successes": 0,
                    "failures": 0,
                    "total_latency_ms": 0.0,
                    "total_tokens_in": 0,
                    "total_tokens_out": 0,
                    "total_cost_usd": 0.0,
                }

            t = self._totals[event.agent_label]
            t["total_calls"] += 1
            if event.success:
                t["successes"] += 1
            else:
                t["failures"] += 1
                self._recent_errors[event.agent_label].append(
                    {
                        "timestamp": event.timestamp,
                        "error_type": event.error_type,
                        "error_message": event.error_message,
                    }
                )
            t["total_latency_ms"] += event.latency_ms
            t["total_tokens_in"] += event.tokens_in
            t["total_tokens_out"] += event.tokens_out
            t["total_cost_usd"] += event.cost_usd

    def get_agent_summary(self, label: str) -> Dict:
        with self._lock:
            t = self._totals.get(label, {})
            if not t:
                return {"status": HealthStatus.UNKNOWN, "label": label}
            calls = t["total_calls"]
            failures = t["failures"]
            error_rate = (failures / calls * 100) if calls > 0 else 0.0
            avg_latency = (t["total_latency_ms"] / calls) if calls > 0 else 0.0

            if error_rate > 30:
                status = HealthStatus.UNHEALTHY
            elif error_rate > 10:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.HEALTHY

            return {
                "label": label,
                "status": status,
                "total_calls": calls,
                "successes": t["successes"],
                "failures": failures,
                "error_rate_pct": round(error_rate, 2),
                "avg_latency_ms": round(avg_latency, 1),
                "total_latency_ms": round(t["total_latency_ms"], 1),
                "total_tokens_in": t["total_tokens_in"],
                "total_tokens_out": t["total_tokens_out"],
                "total_cost_usd": round(t["total_cost_usd"], 6),
                "avg_cost_per_call": round(t["total_cost_usd"] / calls, 6) if calls else 0,
            }

    def get_all_summaries(self) -> Dict[str, Dict]:
        with self._lock:
            return {label: self._get_summary_unlocked(label) for label in self._totals}

    def _get_summary_unlocked(self, label: str) -> Dict:
        t = self._totals.get(label, {})
        if not t:
            return {"status": HealthStatus.UNKNOWN, "label": label}
        calls = t["total_calls"]
        failures = t["failures"]
        error_rate = (failures / calls * 100) if calls > 0 else 0.0
        avg_latency = (t["total_latency_ms"] / calls) if calls > 0 else 0.0
        if error_rate > 30:
            status = HealthStatus.UNHEALTHY
        elif error_rate > 10:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY
        return {
            "label": label,
            "status": status,
            "total_calls": calls,
            "successes": t["successes"],
            "failures": failures,
            "error_rate_pct": round(error_rate, 2),
            "avg_latency_ms": round(avg_latency, 1),
            "total_latency_ms": round(t["total_latency_ms"], 1),
            "total_tokens_in": t["total_tokens_in"],
            "total_tokens_out": t["total_tokens_out"],
            "total_cost_usd": round(t["total_cost_usd"], 6),
            "avg_cost_per_call": round(t["total_cost_usd"] / calls, 6) if calls else 0,
        }

    def get_recent_errors(self, label: str, limit: int = 10) -> List[Dict]:
        with self._lock:
            return list(self._recent_errors.get(label, []))[-limit:]

    def get_cost_breakdown(self) -> Dict:
        with self._lock:
            breakdown = {}
            for label, t in self._totals.items():
                breakdown[label] = {
                    "total_cost_usd": round(t["total_cost_usd"], 6),
                    "total_tokens_in": t["total_tokens_in"],
                    "total_tokens_out": t["total_tokens_out"],
                    "avg_cost_per_call": round(t["total_cost_usd"] / t["total_calls"], 6) if t["total_calls"] else 0,
                }
            return breakdown


# ── Basic health check (unchanged) ──────────────────────────────────────────
class HealthCheck:
    def __init__(self, name: str, check_func: Callable, interval_seconds: int = 60, timeout_seconds: int = 10):
        self.name = name
        self.check_func = check_func
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self.last_check = None
        self.last_status = HealthStatus.UNKNOWN
        self.last_error = None
        self.consecutive_failures = 0


# ── Main system ─────────────────────────────────────────────────────────────
class HealthCheckSystem:
    def __init__(self, storage_file: Optional[str] = None):
        self.storage_file = storage_file or str(data_dir() / "health_checks.json")
        self._ensure_storage_dir()

        self.checks: Dict[str, HealthCheck] = {}
        self.results: Dict[str, Dict] = {}
        self.running = False
        self._thread = None

        self.agent_metrics = AgentMetricsStore()
        self.cost_budget = CostBudget()
        self._request_log: deque = deque(maxlen=1000)

        self._load_results()
        self._setup_default_checks()

    # ── storage ─────────────────────────────────────────────────────────────
    def _ensure_storage_dir(self):
        Path(self.storage_file).parent.mkdir(exist_ok=True)

    def _load_results(self):
        try:
            if Path(self.storage_file).exists():
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    self.results = json.load(f)
        except Exception as e:
            logger.error("Failed to load health results: %s", e)

    def _save_results(self):
        try:
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save health results: %s", e)

    # ── default checks ──────────────────────────────────────────────────────
    def _setup_default_checks(self):
        self.register_check("ollama", self._check_ollama, interval_seconds=120)
        self.register_check("disk_space", self._check_disk_space, interval_seconds=300)
        self.register_check("memory_usage", self._check_memory_usage, interval_seconds=120)

    def _check_ollama(self) -> Dict:
        import requests

        try:
            from ..config import OLLAMA_URL

            response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            if response.status_code == 200:
                return {"status": HealthStatus.HEALTHY, "message": "Ollama is running"}
            return {"status": HealthStatus.DEGRADED, "message": f"Ollama responded with {response.status_code}"}
        except Exception as e:
            return {"status": HealthStatus.UNHEALTHY, "message": f"Ollama connection failed: {e}"}

    def _check_disk_space(self) -> Dict:
        import shutil

        try:
            import psutil

            partitions = psutil.disk_partitions()
            if not partitions:
                return {"status": "unknown", "error": "No disk partitions found"}
            usage = shutil.disk_usage(partitions[0].mountpoint)
            free_gb = usage.free / (1024**3)
            total_gb = usage.total / (1024**3)
            percent_free = (usage.free / usage.total) * 100
            if percent_free < 10:
                status = HealthStatus.UNHEALTHY
            elif percent_free < 25:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.HEALTHY
            return {
                "status": status,
                "message": f"Free: {free_gb:.1f}GB / {total_gb:.1f}GB ({percent_free:.1f}%)",
                "free_gb": free_gb,
                "total_gb": total_gb,
                "percent_free": percent_free,
            }
        except Exception as e:
            return {"status": HealthStatus.UNKNOWN, "message": f"Disk check failed: {e}"}

    def _check_memory_usage(self) -> Dict:
        try:
            import psutil

            memory = psutil.virtual_memory()
            if memory.percent > 90:
                status = HealthStatus.UNHEALTHY
            elif memory.percent > 75:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.HEALTHY
            return {
                "status": status,
                "message": f"Memory: {memory.percent}% used",
                "percent_used": memory.percent,
                "available_gb": memory.available / (1024**3),
            }
        except ImportError:
            return {"status": HealthStatus.UNKNOWN, "message": "psutil not installed"}
        except Exception as e:
            return {"status": HealthStatus.UNKNOWN, "message": f"Memory check failed: {e}"}

    # ── check registration & execution ──────────────────────────────────────
    def register_check(self, name: str, check_func: Callable, interval_seconds: int = 60, timeout_seconds: int = 10):
        self.checks[name] = HealthCheck(
            name=name, check_func=check_func, interval_seconds=interval_seconds, timeout_seconds=timeout_seconds
        )
        logger.info("Health check registered: %s", name)

    def run_check(self, name: str) -> Dict:
        if name not in self.checks:
            return {"status": HealthStatus.UNKNOWN, "message": f"Check not found: {name}"}
        check = self.checks[name]
        try:
            result = check.check_func()
            check.last_check = datetime.now().isoformat()
            check.last_status = result.get("status", HealthStatus.UNKNOWN)
            check.last_error = None
            check.consecutive_failures = 0
            self.results[name] = {
                "status": result["status"],
                "message": result.get("message", ""),
                "last_check": check.last_check,
                "details": result,
            }
            self._save_results()
            return result
        except Exception as e:
            check.last_check = datetime.now().isoformat()
            check.last_status = HealthStatus.UNHEALTHY
            check.last_error = str(e)
            check.consecutive_failures += 1
            self.results[name] = {
                "status": HealthStatus.UNHEALTHY,
                "message": str(e),
                "last_check": check.last_check,
                "error": str(e),
            }
            self._save_results()
            logger.error("Health check failed: %s - %s", name, e)
            return {"status": HealthStatus.UNHEALTHY, "message": str(e)}

    def run_all_checks(self) -> Dict[str, Dict]:
        return {name: self.run_check(name) for name in self.checks}

    def get_overall_status(self) -> str:
        statuses = [check.last_status for check in self.checks.values()]
        if not statuses:
            return HealthStatus.UNKNOWN
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        elif all(s == HealthStatus.HEALTHY for s in statuses if s != HealthStatus.UNKNOWN):
            # all() on an empty filtered list would be True; require at least
            # one non-UNKNOWN signal before declaring HEALTHY.
            if any(s == HealthStatus.HEALTHY for s in statuses):
                return HealthStatus.HEALTHY
            return HealthStatus.UNKNOWN
        return HealthStatus.UNKNOWN

    # ── agent tracking API ──────────────────────────────────────────────────
    def record_agent_event(self, event: AgentEvent):
        if not self.cost_budget.can_spend(event.cost_usd):
            logger.warning("Cost budget exceeded — event for %s blocked ($%.4f)", event.agent_label, event.cost_usd)
            return
        self.cost_budget.record_spend(event.cost_usd)
        self.agent_metrics.record(event)
        self._request_log.append(asdict(event))

    def wrap_agent_call(self, label: str, name: str, provider: str = "ollama", tokens_in: int = 0, tokens_out: int = 0):
        """Context-manager-style decorator for wrapping agent.execute()."""
        store = self

        class _Wrapper:
            def __init__(self):
                self.start_time = 0.0
                self.tokens_in = tokens_in
                self.tokens_out = tokens_out
                self.provider = provider
                self.error = None

            def set_tokens(self, tokens_in: int = None, tokens_out: int = None, provider: str = None):
                """Forward actual token counts once known (e.g. after LLM call)."""
                if tokens_in is not None:
                    try:
                        self.tokens_in = int(tokens_in)
                    except (TypeError, ValueError):
                        pass
                if tokens_out is not None:
                    try:
                        self.tokens_out = int(tokens_out)
                    except (TypeError, ValueError):
                        pass
                if provider:
                    self.provider = provider
                return self

            # Back-compat alias
            update_tokens = set_tokens

            def __enter__(self):
                self.start_time = time.time()
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                latency_ms = (time.time() - self.start_time) * 1000
                success = exc_type is None
                error_type = exc_type.__name__ if exc_type else None
                error_msg = str(exc_val) if exc_val else None
                cost = _estimate_cost(self.tokens_in, self.tokens_out, self.provider)

                event = AgentEvent(
                    agent_label=label,
                    agent_name=name,
                    success=success,
                    latency_ms=round(latency_ms, 2),
                    tokens_in=self.tokens_in,
                    tokens_out=self.tokens_out,
                    cost_usd=round(cost, 6),
                    provider=self.provider,
                    error_type=error_type,
                    error_message=error_msg,
                )
                store.record_agent_event(event)
                return False  # don't suppress exceptions

        return _Wrapper()

    def log_token_usage(self, label: str, tokens_in: int, tokens_out: int, provider: str = "ollama"):
        cost = _estimate_cost(tokens_in, tokens_out, provider)
        event = AgentEvent(
            agent_label=label,
            agent_name=label,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=round(cost, 6),
            provider=provider,
        )
        self.record_agent_event(event)

    # ── agent dashboard ─────────────────────────────────────────────────────
    def get_agent_dashboard(self) -> Dict:
        summaries = self.agent_metrics.get_all_summaries()
        cost_breakdown = self.agent_metrics.get_cost_breakdown()

        total_calls = sum(s.get("total_calls", 0) for s in summaries.values())
        total_failures = sum(s.get("failures", 0) for s in summaries.values())
        total_cost = sum(s.get("total_cost_usd", 0) for s in summaries.values())
        total_tokens = sum(s.get("total_tokens_in", 0) + s.get("total_tokens_out", 0) for s in summaries.values())

        unhealthy_agents = [l for l, s in summaries.items() if s.get("status") == HealthStatus.UNHEALTHY]
        degraded_agents = [l for l, s in summaries.items() if s.get("status") == HealthStatus.DEGRADED]

        return {
            "overall_status": self.get_overall_status(),
            "system_checks": {
                name: {"status": r["status"], "message": r.get("message", "")} for name, r in self.results.items()
            },
            "agents": summaries,
            "cost": {
                "total_usd": round(total_cost, 6),
                "remaining_usd": round(self.cost_budget.remaining(), 6),
                "daily_limit_usd": self.cost_budget.daily_limit_usd,
                "per_request_limit_usd": self.cost_budget.per_request_limit_usd,
                "breakdown_by_agent": cost_breakdown,
            },
            "totals": {
                "total_calls": total_calls,
                "total_failures": total_failures,
                "global_error_rate_pct": round((total_failures / total_calls * 100) if total_calls > 0 else 0.0, 2),
                "total_tokens": total_tokens,
                "total_cost_usd": round(total_cost, 6),
            },
            "alerts": {
                "unhealthy_agents": unhealthy_agents,
                "degraded_agents": degraded_agents,
                "budget_low": self.cost_budget.remaining() < 1.0,
            },
        }

    def get_agent_errors(self, label: str, limit: int = 10) -> List[Dict]:
        return self.agent_metrics.get_recent_errors(label, limit)

    # ── background loop ─────────────────────────────────────────────────────
    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Health check system started")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Health check system stopped")

    def _run_loop(self):
        while self.running:
            try:
                for name, check in self.checks.items():
                    if check.last_check:
                        try:
                            last = datetime.fromisoformat(check.last_check)
                            if (datetime.now() - last).total_seconds() >= check.interval_seconds:
                                self.run_check(name)
                        except Exception:
                            self.run_check(name)
                    else:
                        self.run_check(name)
                time.sleep(30)
            except Exception as e:
                logger.error("Health check loop error: %s", e)
                time.sleep(30)

    # ── full dashboard (backward-compat + new metrics) ──────────────────────
    def get_dashboard(self) -> Dict:
        agent_dash = self.get_agent_dashboard()
        return {
            "overall_status": agent_dash["overall_status"],
            "checks": agent_dash["system_checks"],
            "total_checks": len(self.checks),
            "healthy": sum(1 for c in agent_dash["system_checks"].values() if c["status"] == HealthStatus.HEALTHY),
            "degraded": sum(1 for c in agent_dash["system_checks"].values() if c["status"] == HealthStatus.DEGRADED),
            "unhealthy": sum(1 for c in agent_dash["system_checks"].values() if c["status"] == HealthStatus.UNHEALTHY),
            "agents": agent_dash["agents"],
            "cost": agent_dash["cost"],
            "totals": agent_dash["totals"],
            "alerts": agent_dash["alerts"],
        }


# ── helpers ─────────────────────────────────────────────────────────────────
def _estimate_cost(tokens_in: int, tokens_out: int, provider: str) -> float:
    rate = MODEL_COST_PER_1K.get(provider, 0.003)
    return ((tokens_in + tokens_out) / 1000.0) * rate


# ── singleton + convenience functions ────────────────────────────────────────
_health_system: Optional[HealthCheckSystem] = None
_health_lock = threading.Lock()


def get_health_system() -> HealthCheckSystem:
    global _health_system
    if _health_system is None:
        with _health_lock:
            if _health_system is None:
                _health_system = HealthCheckSystem()
    return _health_system


def run_check(name: str) -> Dict:
    return get_health_system().run_check(name)


def run_all_checks() -> Dict[str, Dict]:
    return get_health_system().run_all_checks()


def get_overall_status() -> str:
    return get_health_system().get_overall_status()


def get_dashboard() -> Dict:
    return get_health_system().get_dashboard()


def get_agent_dashboard() -> Dict:
    return get_health_system().get_agent_dashboard()


def record_agent_event(event: AgentEvent):
    get_health_system().record_agent_event(event)


def wrap_agent_call(label: str, name: str, provider: str = "ollama"):
    return get_health_system().wrap_agent_call(label, name, provider)


def log_token_usage(label: str, tokens_in: int, tokens_out: int, provider: str = "ollama"):
    get_health_system().log_token_usage(label, tokens_in, tokens_out, provider)
