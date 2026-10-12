# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Execution Traces — P2 Observability

Full execution trace per task:
- Trace = one user request through the system
- Span = one unit of work (agent, tool, LLM call, etc.)
- Parent-child relationships for nested execution
- Token/cost tracking per span
- Automatic wiring into agent dispatch, tool execution, LLM calls
"""

import json
import threading
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..logging_config import get_logger
from .runtime_paths import orbit_data_dir

logger = get_logger(__name__)

# ── Context variable for automatic parent tracking ───────────────────────────

_current_span_id: ContextVar[Optional[str]] = ContextVar("current_span_id", default=None)
_span_stack: ContextVar[tuple] = ContextVar("orbit_span_stack", default=())


# ── Data Models ──────────────────────────────────────────────────────────────


@dataclass
class Span:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:10])
    trace_id: str = ""
    parent_id: str = ""
    name: str = ""
    span_type: str = ""  # "agent" | "tool" | "llm" | "reasoning" | "custom"
    agent_id: str = ""  # which agent (TAS, COD, etc.)
    tool_name: str = ""  # which tool (file_tools, web_search, etc.)
    provider: str = ""  # which LLM provider (ollama, openrouter, etc.)
    model: str = ""  # which model
    status: str = "running"  # "running" | "ok" | "error" | "timeout"
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    duration_ms: float = 0.0
    input_preview: str = ""  # first 500 chars of input
    output_preview: str = ""  # first 500 chars of output
    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    metadata: Dict = field(default_factory=dict)


@dataclass
class Trace:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    query: str = ""
    status: str = "running"  # "running" | "ok" | "error" | "timeout"
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    total_duration_ms: float = 0.0
    spans: List[Dict] = field(default_factory=list)
    result: str = ""
    error: str = ""
    agent_id: str = ""  # primary agent
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_usd: float = 0.0
    span_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ── ExecutionTracer ─────────────────────────────────────────────────────────


class ExecutionTracer:
    """
    Full execution tracing through the ORBIT pipeline.

    Usage:
        tracer = get_execution_tracer()
        trace_id = tracer.start_trace("user query")
        span_id = tracer.start_span(trace_id, "agent_dispatch", "agent", agent_id="TAS")
        # ... do work ...
        tracer.end_span(trace_id, span_id, status="ok", output_preview="result...")
        tracer.end_trace(trace_id, status="ok", result="final answer")
    """

    def __init__(self, storage_dir: Optional[str] = None, max_traces: int = 1000):
        self._storage_dir = Path(storage_dir or str(orbit_data_dir() / "traces"))
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._max_traces = max_traces
        self._lock = threading.Lock()
        self._traces: List[Trace] = []
        self._active: Dict[str, Trace] = {}
        self._load()

    def _load(self):
        """Load recent traces from disk."""
        try:
            trace_file = self._storage_dir / "traces.json"
            if trace_file.exists():
                data = json.loads(trace_file.read_text(encoding="utf-8"))
                for t in data.get("traces", []):
                    trace = Trace(**{k: v for k, v in t.items() if k in Trace.__dataclass_fields__})
                    self._traces.append(trace)
        except Exception as e:
            logger.error("Failed to load execution traces: %s", e)

    def _save(self):
        """Persist traces to disk (recent N)."""
        try:
            with self._lock:
                recent = list(self._traces[-self._max_traces :])
        except Exception as e:
            logger.error("Failed to snapshot execution traces: %s", e)
            return
        try:
            trace_file = self._storage_dir / "traces.json"
            data = {"traces": [asdict(t) for t in recent]}
            trace_file.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.error("Failed to save execution traces: %s", e)

    def _save_async(self, snapshot: List[Trace]) -> None:
        """Write a snapshot in a background daemon thread (non-blocking)."""
        try:
            trace_file = self._storage_dir / "traces.json"
            data = {"traces": [asdict(t) for t in snapshot]}

            def _write():
                try:
                    trace_file.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
                except Exception as e:
                    logger.error("Failed to save execution traces: %s", e)

            threading.Thread(target=_write, daemon=True).start()
        except Exception as e:
            logger.error("Failed to queue execution trace save: %s", e)

    # ── Trace lifecycle ──────────────────────────────────────────────────

    def start_trace(self, query: str = "", agent_id: str = "") -> str:
        """Start a new top-level trace for a user request."""
        trace = Trace(query=query[:1000], agent_id=agent_id)
        with self._lock:
            self._active[trace.id] = trace
        logger.debug("Trace started: %s query=%s", trace.id, query[:80])
        return trace.id

    def end_trace(self, trace_id: str, status: str = "ok", result: str = "", error: str = ""):
        """End a top-level trace and persist it."""
        with self._lock:
            trace = self._active.pop(trace_id, None)
            if not trace:
                return
            trace.end_time = time.time()
            trace.total_duration_ms = round((trace.end_time - trace.start_time) * 1000, 2)
            trace.status = status
            trace.result = result[:2000]
            trace.error = error[:500]
            # Aggregate span stats
            trace.span_count = len(trace.spans)
            trace.total_tokens_in = sum(s.get("tokens_in", 0) for s in trace.spans)
            trace.total_tokens_out = sum(s.get("tokens_out", 0) for s in trace.spans)
            trace.total_cost_usd = round(sum(s.get("cost_usd", 0) for s in trace.spans), 6)
            self._traces.append(trace)
            if len(self._traces) > self._max_traces:
                self._traces = self._traces[-self._max_traces :]
            snapshot = list(self._traces)
        self._save_async(snapshot)
        logger.debug("Trace ended: %s status=%s duration=%.0fms", trace_id, status, trace.total_duration_ms)

    # ── Span lifecycle ───────────────────────────────────────────────────

    def start_span(
        self,
        trace_id: str,
        name: str,
        span_type: str = "",
        parent_id: str = "",
        agent_id: str = "",
        tool_name: str = "",
        provider: str = "",
        model: str = "",
        input_preview: str = "",
        metadata: Dict = None,
    ) -> str:
        """Start a new span within a trace."""
        stack = _span_stack.get() or ()
        parent_default = parent_id or (stack[-1] if stack else (_current_span_id.get() or ""))
        span = Span(
            trace_id=trace_id,
            parent_id=parent_default,
            name=name,
            span_type=span_type,
            agent_id=agent_id,
            tool_name=tool_name,
            provider=provider,
            model=model,
            input_preview=input_preview[:500] if input_preview else "",
            metadata=metadata or {},
        )
        with self._lock:
            trace = self._active.get(trace_id)
            if trace:
                if len(trace.spans) >= 500:
                    trace.spans = trace.spans[-500:]
                trace.spans.append(asdict(span))
        _span_stack.set(tuple(stack) + (span.id,))
        _current_span_id.set(span.id)
        logger.debug("Span started: %s type=%s name=%s", span.id, span_type, name)
        return span.id

    def end_span(
        self,
        trace_id: str,
        span_id: str,
        status: str = "ok",
        error: str = "",
        output_preview: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        metadata: Dict = None,
    ):
        """End a span and update its stats."""
        with self._lock:
            trace = self._active.get(trace_id)
            if not trace:
                stack = _span_stack.get() or ()
                if span_id in stack:
                    _span_stack.set(tuple(s for s in stack if s != span_id))
                    _current_span_id.set(_span_stack.get()[-1] if _span_stack.get() else None)
                return
            for span_dict in trace.spans:
                if span_dict.get("id") == span_id:
                    span_dict["end_time"] = time.time()
                    span_dict["duration_ms"] = round((span_dict["end_time"] - span_dict["start_time"]) * 1000, 2)
                    span_dict["status"] = status
                    span_dict["error"] = error[:500]
                    if output_preview:
                        span_dict["output_preview"] = output_preview[:500]
                    if tokens_in:
                        span_dict["tokens_in"] = tokens_in
                    if tokens_out:
                        span_dict["tokens_out"] = tokens_out
                    if cost_usd:
                        span_dict["cost_usd"] = cost_usd
                    if metadata:
                        span_dict["metadata"].update(metadata)
                    break
        stack = _span_stack.get() or ()
        if span_id in stack:
            new_stack = tuple(s for s in stack if s != span_id)
            _span_stack.set(new_stack)
            _current_span_id.set(new_stack[-1] if new_stack else None)
        else:
            _current_span_id.set(None)

    # ── Convenience: context-manager style ───────────────────────────────

    def trace_span(self, trace_id: str, name: str, span_type: str = "", **kwargs) -> "_SpanContext":
        """Context manager for automatic span start/end."""
        return _SpanContext(self, trace_id, name, span_type, **kwargs)

    # ── Query / Stats ────────────────────────────────────────────────────

    def get_trace(self, trace_id: str) -> Optional[Dict]:
        with self._lock:
            for t in self._traces:
                if t.id == trace_id:
                    return asdict(t)
            # Also check active
            if trace_id in self._active:
                return asdict(self._active[trace_id])
        return None

    def list_traces(self, limit: int = 50, status: str = "", agent_id: str = "", query: str = "") -> List[Dict]:
        with self._lock:
            results = list(self._traces)
        if status:
            results = [t for t in results if t.status == status]
        if agent_id:
            results = [t for t in results if t.agent_id == agent_id]
        if query:
            q = query.lower()
            results = [t for t in results if q in t.query.lower()]
        # Most recent first
        results = results[-limit:]
        results.reverse()
        return [asdict(t) for t in results]

    def get_slowest(self, limit: int = 10) -> List[Dict]:
        with self._lock:
            sorted_t = sorted(self._traces, key=lambda t: -t.total_duration_ms)
            return [asdict(t) for t in sorted_t[:limit]]

    def get_errors(self, limit: int = 20) -> List[Dict]:
        with self._lock:
            errors = [t for t in self._traces if t.status == "error"]
            return [asdict(t) for t in errors[-limit:]]

    def get_stats(self) -> Dict:
        with self._lock:
            traces = list(self._traces)
        if not traces:
            return {
                "total_traces": 0,
                "active_traces": len(self._active),
                "total_spans": 0,
            }
        durations = [t.total_duration_ms for t in traces if t.total_duration_ms > 0]
        errors = sum(1 for t in traces if t.status == "error")
        total_spans = sum(t.span_count for t in traces)
        total_cost = sum(t.total_cost_usd for t in traces)
        total_tokens = sum(t.total_tokens_in + t.total_tokens_out for t in traces)

        agent_counts = defaultdict(int)
        agent_errors = defaultdict(int)
        span_type_counts = defaultdict(int)
        for t in traces:
            if t.agent_id:
                agent_counts[t.agent_id] += 1
            if t.status == "error" and t.agent_id:
                agent_errors[t.agent_id] += 1
            for s in t.spans:
                st = s.get("span_type", "unknown")
                span_type_counts[st] += 1

        avg_duration = sum(durations) / len(durations) if durations else 0
        p50 = sorted(durations)[len(durations) // 2] if durations else 0
        p95_idx = int(len(durations) * 0.95)
        p95 = sorted(durations)[min(p95_idx, len(durations) - 1)] if durations else 0

        return {
            "total_traces": len(traces),
            "active_traces": len(self._active),
            "total_spans": total_spans,
            "error_traces": errors,
            "error_rate": round(errors / max(len(traces), 1) * 100, 1),
            "avg_duration_ms": round(avg_duration, 1),
            "p50_duration_ms": round(p50, 1),
            "p95_duration_ms": round(p95, 1),
            "total_cost_usd": round(total_cost, 6),
            "total_tokens": total_tokens,
            "by_agent": dict(sorted(agent_counts.items(), key=lambda x: -x[1])[:15]),
            "errors_by_agent": dict(agent_errors),
            "by_span_type": dict(span_type_counts),
        }

    def clear(self):
        """Clear all traces (active + stored)."""
        with self._lock:
            self._traces.clear()
            self._active.clear()
        self._save()


# ── Span Context Manager ────────────────────────────────────────────────────


class _SpanContext:
    """Context manager for automatic span lifecycle."""

    def __init__(self, tracer: ExecutionTracer, trace_id: str, name: str, span_type: str = "", **kwargs):
        self.tracer = tracer
        self.trace_id = trace_id
        self.name = name
        self.span_type = span_type
        self.kwargs = kwargs
        self.span_id = None

    def __enter__(self):
        self.span_id = self.tracer.start_span(self.trace_id, self.name, self.span_type, **self.kwargs)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        status = "ok"
        error = ""
        if exc_type:
            status = "error"
            error = f"{exc_type.__name__}: {exc_val}"[:500]
        self.tracer.end_span(self.trace_id, self.span_id, status=status, error=error)
        return False  # don't suppress exceptions


# ── Singleton ───────────────────────────────────────────────────────────────

_tracer: Optional[ExecutionTracer] = None
_tracer_lock = threading.Lock()


def get_execution_tracer() -> ExecutionTracer:
    global _tracer
    if _tracer is None:
        with _tracer_lock:
            if _tracer is None:
                _tracer = ExecutionTracer()
    return _tracer
