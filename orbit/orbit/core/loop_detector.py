# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Loop Detector — P3 Observability

Detects when agents/tools get stuck in retry loops:
- LLM provider retry loops (same provider failing repeatedly)
- Agent dispatch loops (same agent re-dispatched with same input)
- Tool call loops (same tool+action called repeatedly)
- Self-correction loops (reflect/debate triggered repeatedly)
- Thread stalls (agent taking too long)
- Provider failure storms (multiple providers failing in sequence)

Auto-recovery: circuit breaker trip, provider switch, escalate to user.
"""

import json
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..logging_config import get_logger
from .runtime_paths import orbit_data_dir

logger = get_logger(__name__)


# ── Loop Types ───────────────────────────────────────────────────────────────


class LoopType(str, Enum):
    LLM_PROVIDER = "llm_provider"  # same provider failing repeatedly
    AGENT_RETRY = "agent_retry"  # same agent re-dispatched
    TOOL_CALL = "tool_call"  # same tool+action in a trace
    SELF_CORRECTION = "self_correction"  # reflect/debate loop
    THREAD_STALL = "thread_stall"  # agent taking too long
    PROVIDER_STORM = "provider_storm"  # multiple providers failing fast


class Severity(str, Enum):
    LOW = "low"  # informational, no action needed
    MEDIUM = "medium"  # pattern detected, monitoring
    HIGH = "high"  # auto-recovery triggered
    CRITICAL = "critical"  # escalated to user, system at risk


# ── Loop Event ───────────────────────────────────────────────────────────────


@dataclass
class LoopEvent:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:10])
    loop_type: str = ""
    component: str = ""  # provider name, agent label, tool name
    trace_id: str = ""  # which execution trace
    details: str = ""
    count: int = 1  # how many times seen in window
    window_ms: float = 0.0  # time window for count
    severity: str = "low"
    action_taken: str = ""  # what recovery was done
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class LoopConfig:
    # LLM provider loop
    llm_provider_threshold: int = 4  # failures before alert
    llm_provider_window_sec: float = 60.0  # time window

    # Agent retry loop
    agent_retry_threshold: int = 3
    agent_retry_window_sec: float = 120.0

    # Tool call loop
    tool_call_threshold: int = 5
    tool_call_window_sec: float = 30.0

    # Self-correction loop
    correction_threshold: int = 3
    correction_window_sec: float = 60.0

    # Thread stall
    stall_threshold_sec: float = 30.0  # single call timeout

    # Provider storm
    storm_threshold: int = 3  # different providers failing
    storm_window_sec: float = 30.0

    max_incidents: int = 500


# ── Loop Detector ────────────────────────────────────────────────────────────


class LoopDetector:
    """
    Real-time loop detection for the ORBIT pipeline.

    Usage:
        detector = get_loop_detector()
        detector.record_llm_failure("ollama", trace_id="abc123")
        detector.record_agent_dispatch("TAS", "write file test.py", trace_id="abc123")
        detector.record_tool_call("file_write", "write", trace_id="abc123")
        detector.record_correction(trace_id="abc123")
        detector.check_stall("TAS", start_time, trace_id="abc123")
    """

    def __init__(self, config: Optional[LoopConfig] = None, storage_dir: Optional[str] = None):
        self._config = config or LoopConfig()
        self._storage_dir = Path(storage_dir or str(orbit_data_dir() / "loops"))
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        # Event windows (component -> deque of timestamps)
        self._llm_failures: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self._agent_dispatches: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self._tool_calls: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self._corrections: deque = deque(maxlen=50)
        self._provider_failures: deque = deque(maxlen=50)

        # Active loops (component -> active incident)
        self._active_loops: Dict[str, Dict] = {}

        # Incident history
        self._incidents: List[Dict] = []
        self._load()

        # Recovery callbacks
        self._recovery_callbacks: Dict[str, Callable] = {}

    def _load(self):
        try:
            f = self._storage_dir / "incidents.json"
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))
                self._incidents = data.get("incidents", [])[-self._config.max_incidents :]
        except Exception as e:
            logger.error("Failed to load loop incidents: %s", e)

    def _save(self):
        try:
            f = self._storage_dir / "incidents.json"
            data = {"incidents": self._incidents[-self._config.max_incidents :]}
            f.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.error("Failed to save loop incidents: %s", e)

    def predict_error(self, component: str, error_type: str = "llm") -> Dict:
        """Predict if an error is likely based on recent patterns."""
        now = time.time()
        prediction = {
            "component": component,
            "error_type": error_type,
            "risk_score": 0.0,
            "factors": [],
            "recommendation": "",
        }

        with self._lock:
            # Check recent failures for this component
            if error_type == "llm":
                failures = self._llm_failures.get(component, [])
                recent = [t for t in failures if t > now - 60]
                if len(recent) >= 2:
                    prediction["risk_score"] = min(0.9, len(recent) * 0.2)
                    prediction["factors"].append(f"{len(recent)} failures in last 60s")
                    prediction["recommendation"] = "Consider switching provider"

            # Check provider storm
            provider_recent = [(p, t) for p, t in self._provider_failures if t > now - 30]
            if len(provider_recent) >= 2:
                unique_providers = set(p for p, t in provider_recent)
                if len(unique_providers) >= 2:
                    prediction["risk_score"] = max(prediction["risk_score"], 0.7)
                    prediction["factors"].append(f"{len(unique_providers)} different providers failing")
                    prediction["recommendation"] = "System-wide issue, check network"

            # Check correction loops
            correction_recent = [t for t in self._corrections if t > now - 60]
            if len(correction_recent) >= 2:
                prediction["risk_score"] = max(prediction["risk_score"], 0.6)
                prediction["factors"].append(f"{len(correction_recent)} self-corrections in 60s")
                prediction["recommendation"] = "Agent stuck in loop, try different approach"

        # Generate recommendation
        if prediction["risk_score"] > 0.7:
            prediction["recommendation"] = "HIGH RISK: " + (
                prediction["recommendation"] or "Immediate intervention needed"
            )
        elif prediction["risk_score"] > 0.4:
            prediction["recommendation"] = "MEDIUM RISK: " + (prediction["recommendation"] or "Monitor closely")
        elif prediction["risk_score"] > 0.1:
            prediction["recommendation"] = "LOW RISK: " + (prediction["recommendation"] or "Normal operation")

        return prediction

    def register_recovery(self, loop_type: str, callback: Callable):
        """Register a recovery callback for a loop type."""
        with self._lock:
            self._recovery_callbacks[loop_type] = callback

    # ── Recording events ────────────────────────────────────────────────

    def record_llm_failure(self, provider: str, trace_id: str = "", error: str = "") -> Optional[Dict]:
        """Record an LLM provider failure. Returns incident if loop detected."""
        now = time.time()
        with self._lock:
            self._llm_failures[provider].append(now)
            self._provider_failures.append((provider, now))

            # Prune old entries
            window = self._config.llm_provider_window_sec
            cutoff = now - window
            self._llm_failures[provider] = deque((t for t in self._llm_failures[provider] if t > cutoff), maxlen=50)

            count = len(self._llm_failures[provider])
            if count >= self._config.llm_provider_threshold:
                return self._emit_incident(
                    LoopType.LLM_PROVIDER,
                    provider,
                    trace_id,
                    f"Provider {provider} failed {count}x in {window:.0f}s",
                    severity=Severity.HIGH if count >= self._config.llm_provider_threshold * 2 else Severity.MEDIUM,
                    action=self._trigger_recovery(LoopType.LLM_PROVIDER, provider),
                )
        return None

    def record_agent_dispatch(self, agent_id: str, query: str, trace_id: str = "") -> Optional[Dict]:
        """Record an agent dispatch. Returns incident if same agent loop detected."""
        now = time.time()
        key = f"{agent_id}:{query[:100]}"
        with self._lock:
            self._agent_dispatches[key].append(now)

            window = self._config.agent_retry_window_sec
            cutoff = now - window
            self._agent_dispatches[key] = deque((t for t in self._agent_dispatches[key] if t > cutoff), maxlen=50)

            count = len(self._agent_dispatches[key])
            if count >= self._config.agent_retry_threshold:
                return self._emit_incident(
                    LoopType.AGENT_RETRY,
                    agent_id,
                    trace_id,
                    f"Agent {agent_id} dispatched {count}x for same query in {window:.0f}s",
                    severity=Severity.MEDIUM,
                    action=self._trigger_recovery(LoopType.AGENT_RETRY, agent_id),
                )
        return None

    def record_tool_call(self, tool_name: str, action: str, trace_id: str = "") -> Optional[Dict]:
        """Record a tool call. Returns incident if loop detected."""
        now = time.time()
        key = f"{tool_name}.{action}"
        with self._lock:
            self._tool_calls[key].append(now)

            window = self._config.tool_call_window_sec
            cutoff = now - window
            self._tool_calls[key] = deque((t for t in self._tool_calls[key] if t > cutoff), maxlen=50)

            count = len(self._tool_calls[key])
            if count >= self._config.tool_call_threshold:
                return self._emit_incident(
                    LoopType.TOOL_CALL,
                    key,
                    trace_id,
                    f"Tool {key} called {count}x in {window:.0f}s",
                    severity=Severity.MEDIUM,
                    action=self._trigger_recovery(LoopType.TOOL_CALL, key),
                )
        return None

    def record_correction(self, trace_id: str = "") -> Optional[Dict]:
        """Record a self-correction event. Returns incident if loop detected."""
        now = time.time()
        with self._lock:
            self._corrections.append(now)

            window = self._config.correction_window_sec
            cutoff = now - window
            self._corrections = deque((t for t in self._corrections if t > cutoff), maxlen=50)

            count = len(self._corrections)
            if count >= self._config.correction_threshold:
                return self._emit_incident(
                    LoopType.SELF_CORRECTION,
                    "self_correction",
                    trace_id,
                    f"Self-correction triggered {count}x in {window:.0f}s",
                    severity=Severity.HIGH,
                    action=self._trigger_recovery(LoopType.SELF_CORRECTION, ""),
                )
        return None

    def check_stall(self, component: str, start_time: float, trace_id: str = "") -> Optional[Dict]:
        """Check if a component has been running too long (stall detection)."""
        elapsed = time.time() - start_time
        if elapsed > self._config.stall_threshold_sec:
            with self._lock:
                return self._emit_incident(
                    LoopType.THREAD_STALL,
                    component,
                    trace_id,
                    f"Component {component} running for {elapsed:.1f}s (threshold: {self._config.stall_threshold_sec}s)",
                    severity=Severity.HIGH,
                    action=self._trigger_recovery(LoopType.THREAD_STALL, component),
                )
        return None

    def record_provider_failure(self, provider: str, trace_id: str = "") -> Optional[Dict]:
        """Record any provider failure (for storm detection)."""
        now = time.time()
        with self._lock:
            self._provider_failures.append((provider, now))

            window = self._config.storm_window_sec
            cutoff = now - window
            self._provider_failures = deque(((p, t) for p, t in self._provider_failures if t > cutoff), maxlen=50)

            unique_providers = set(p for p, _ in self._provider_failures)
            if len(unique_providers) >= self._config.storm_threshold:
                return self._emit_incident(
                    LoopType.PROVIDER_STORM,
                    "multi_provider",
                    trace_id,
                    f"Provider storm: {len(unique_providers)} providers failing in {window:.0f}s ({', '.join(unique_providers)})",
                    severity=Severity.CRITICAL,
                    action=self._trigger_recovery(LoopType.PROVIDER_STORM, ""),
                )
        return None

    # ── Recovery ────────────────────────────────────────────────────────

    def _trigger_recovery(self, loop_type: str, component: str) -> str:
        """Trigger auto-recovery for a detected loop."""
        actions = []

        # Circuit breaker trip for provider loops
        if loop_type == LoopType.LLM_PROVIDER:
            try:
                from .circuit_breaker import get_circuit_breaker

                cb = get_circuit_breaker(component)
                if cb.state.value != "open":
                    cb._on_failure_sync(Exception(f"loop_detector: provider {component} in failure loop"))
                    actions.append(f"circuit_breaker_tripped({component})")
            except Exception:
                pass

        # Provider switch for storms
        if loop_type == LoopType.PROVIDER_STORM:
            actions.append("escalate_to_user")

        # Callback if registered
        with self._lock:
            callback = self._recovery_callbacks.get(loop_type)
        if callback:
            try:
                callback(component)
                actions.append(f"callback({loop_type})")
            except Exception:
                pass

        action_str = "; ".join(actions) if actions else "none"
        logger.warning("Loop recovery [%s] %s: %s", loop_type, component, action_str)
        return action_str

    # ── Incident management ─────────────────────────────────────────────

    def _emit_incident(
        self,
        loop_type: str,
        component: str,
        trace_id: str,
        details: str,
        severity: Severity = Severity.LOW,
        action: str = "",
    ) -> Dict:
        """Create and store a loop incident."""
        incident = {
            "id": str(uuid.uuid4())[:10],
            "loop_type": loop_type,
            "component": component,
            "trace_id": trace_id,
            "details": details,
            "severity": severity.value,
            "action_taken": action,
            "timestamp": datetime.now().isoformat(),
        }

        # Deduplicate: don't emit same loop_type+component within 10s
        dedup_key = f"{loop_type}:{component}"
        now = time.time()
        if dedup_key in self._active_loops:
            last = self._active_loops[dedup_key].get("_last_emit", 0)
            if now - last < 10:
                return self._active_loops[dedup_key]

        incident["_last_emit"] = now
        self._active_loops[dedup_key] = incident
        self._incidents.append(incident)

        if len(self._incidents) > self._config.max_incidents:
            self._incidents = self._incidents[-self._config.max_incidents :]

        self._save()
        logger.warning("Loop detected [%s] %s: %s (action: %s)", loop_type, component, details, action)
        return incident

    def clear_active(self, loop_type: str = "", component: str = ""):
        """Clear an active loop (e.g., after manual resolution)."""
        with self._lock:
            if loop_type and component:
                key = f"{loop_type}:{component}"
                self._active_loops.pop(key, None)
            elif loop_type:
                self._active_loops = {k: v for k, v in self._active_loops.items() if not k.startswith(loop_type)}
            else:
                self._active_loops.clear()

    # ── Query ───────────────────────────────────────────────────────────

    def get_active_loops(self) -> List[Dict]:
        """Get all currently active loops."""
        with self._lock:
            return [{k: v for k, v in inc.items() if k != "_last_emit"} for inc in self._active_loops.values()]

    def get_incidents(self, limit: int = 50, loop_type: str = "", severity: str = "") -> List[Dict]:
        """Get recent incidents."""
        with self._lock:
            results = list(self._incidents)
        if loop_type:
            results = [i for i in results if i.get("loop_type") == loop_type]
        if severity:
            results = [i for i in results if i.get("severity") == severity]
        return [{k: v for k, v in i.items() if k != "_last_emit"} for i in results[-limit:]]

    def get_stats(self) -> Dict:
        """Get loop detection statistics."""
        with self._lock:
            incidents = list(self._incidents)
            active_loops = len(self._active_loops)

        if not incidents:
            return {"total_incidents": 0, "active_loops": active_loops}

        by_type = defaultdict(int)
        by_severity = defaultdict(int)
        by_component = defaultdict(int)
        for inc in incidents:
            by_type[inc["loop_type"]] += 1
            by_severity[inc["severity"]] += 1
            by_component[inc["component"]] += 1

        return {
            "total_incidents": len(incidents),
            "active_loops": active_loops,
            "by_type": dict(by_type),
            "by_severity": dict(by_severity),
            "by_component": dict(sorted(by_component.items(), key=lambda x: -x[1])[:15]),
            "recent_5min": sum(1 for i in incidents if self._is_recent(i, 300)),
        }

    def _is_recent(self, incident: Dict, seconds: float) -> bool:
        try:
            ts = datetime.fromisoformat(incident["timestamp"])
            return (datetime.now() - ts).total_seconds() < seconds
        except Exception:
            return False

    def get_health(self) -> Dict:
        """Quick health check: are any critical loops active?"""
        active = self.get_active_loops()
        critical = [a for a in active if a.get("severity") in ("high", "critical")]
        return {
            "healthy": len(critical) == 0,
            "active_count": len(active),
            "critical_count": len(critical),
            "critical": critical[:5],
        }


# ── Singleton ───────────────────────────────────────────────────────────────

_detector: Optional[LoopDetector] = None
_detector_lock = threading.Lock()


def get_loop_detector() -> LoopDetector:
    global _detector
    if _detector is None:
        with _detector_lock:
            if _detector is None:
                _detector = LoopDetector()
    return _detector
