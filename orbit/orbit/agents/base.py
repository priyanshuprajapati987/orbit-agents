# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class BaseAgent:
    label = "GEN"
    name = "Generic"
    color = "#00d4ff"
    description = "Base agent"

    def __init__(self, assistant=None):
        self.assistant = assistant
        self.history = []
        self._lock = threading.Lock()
        self.metrics = {"calls": 0, "total_ms": 0, "avg_ms": 0, "last_call": None}

    def get_system_prompt(self, context=None):
        try:
            from ..core.personality import get_system_prompt as _personality_prompt

            task_type = getattr(self, "_task_type", "general")
            return _personality_prompt(self.DEFAULT_PROMPT, task_type)
        except Exception:
            return getattr(self, "DEFAULT_PROMPT", f"You are {getattr(self, 'name', 'an AI')} agent.")

    def get_learning_context(self, text: str) -> str:
        """Get improvement context from past mistakes before executing."""
        try:
            from ..core.learning_system import get_improvement_context

            return get_improvement_context(self.label, self.name, text)
        except Exception:
            return ""

    def record_mistake(self, text: str, expected: str, actual: str, error: str = "", category: str = "reasoning_error"):
        """Record a failure for future learning."""
        try:
            from ..core.learning_system import record_mistake as _record

            _record(self.label, self.name, text, expected, actual, error, category)
        except Exception:
            logger.exception("BaseAgent.record_mistake failed for %s", self.label)

    def record_success(self, text: str, output: str):
        """Record a success for pattern learning."""
        try:
            from ..core.learning_system import record_success as _record

            _record(self.label, self.name, text, output)
        except Exception:
            logger.exception("BaseAgent.record_success failed for %s", self.label)

    def can_handle(self, text, intent=None, confidence=0.0):
        return False

    def execute(self, text, context=None):
        start = time.time()

        try:
            result = self._run(text, context)
        except Exception:
            logger.exception("Agent %s failed", self.label)
            result = f"Agent {self.label} encountered an error. Check server logs."

        elapsed = (time.time() - start) * 1000
        with self._lock:
            self.metrics["calls"] += 1
            self.metrics["last_call"] = datetime.now().isoformat()
            self.metrics["total_ms"] += elapsed
            self.metrics["avg_ms"] = self.metrics["total_ms"] / self.metrics["calls"]

        return result

    def _run(self, text, context=None):
        raise NotImplementedError

    def get_metrics(self):
        with self._lock:
            return dict(self.metrics)
