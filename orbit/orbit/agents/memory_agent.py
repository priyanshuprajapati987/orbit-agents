# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
from datetime import datetime

from .. import memory as memory_module
from ..core.memory_store import get_shared_memory, share_memory, recall_shared
from .base import BaseAgent

MEMORY_AGENT_PROMPT = """You are the MEMORY AGENT. Save and recall context for the multi-agent pipeline.
Extract key facts, decisions, and results from executions.
Store them for later recall by other agents via the shared memory pool."""


class PipelineMemoryAgent(BaseAgent):
    label = "PMA"
    name = "PipelineMemory"
    color = "#8b5cf6"
    description = "Saves/recalls pipeline execution context with cross-agent sharing"

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._pool = get_shared_memory()

    def can_handle(self, text, intent=None, confidence=0.0):
        return True

    def save_result(self, task_id: int, subtask_id: int, result: str, context: str = ""):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "task_id": task_id,
            "subtask_id": subtask_id,
            "result": result[:500],
            "context": context,
        }
        memory_module.save_conversation(
            f"[Pipeline Task#{task_id} Sub#{subtask_id}] {context}",
            result[:500],
            context="pipeline",
        )

        share_memory(
            agent_id=self.label,
            namespace="pipeline",
            key=f"task{task_id}_sub{subtask_id}",
            value=entry,
            tags=["pipeline", f"task_{task_id}"],
        )

        return entry

    def recall(self, task_id: int = None, query: str = "") -> str:
        if task_id:
            results = recall_shared(
                agent_id=self.label,
                query=f"task {task_id}",
                namespaces=["pipeline", "global"],
                limit=5,
            )
            if results:
                lines = []
                for r in results:
                    val = r.get("value", {})
                    ts = val.get("timestamp", "?")[:16]
                    res = val.get("result", str(val))[:200]
                    lines.append(f"[{ts}] {res}")
                return "\n".join(lines)

        if query:
            results = recall_shared(
                agent_id=self.label,
                query=query,
                namespaces=["pipeline", "global"],
                limit=5,
            )
            if results:
                lines = []
                for r in results:
                    val = r.get("value", {})
                    score = r.get("relevance_score", 0)
                    res = val.get("result", str(val))[:200] if isinstance(val, dict) else str(val)[:200]
                    lines.append(f"[{score:.2f}] {res}")
                return "\n".join(lines)

        memories = memory_module.get_all()
        relevant = []
        for m in memories:
            if task_id and f"Task#{task_id}" in m.get("user", ""):
                relevant.append(m)
            elif query and query.lower() in m.get("user", "").lower():
                relevant.append(m)
        if not relevant:
            return "No relevant pipeline memories found."
        lines = []
        for m in relevant[-5:]:
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m.get('assistant', '')[:200]}")
        return "\n".join(lines)

    def summarize_pipeline(self, task_id: int) -> str:
        results = recall_shared(
            agent_id=self.label,
            query=f"pipeline task {task_id}",
            namespaces=["pipeline"],
            limit=50,
        )
        if results:
            summary = []
            for r in results:
                val = r.get("value", {})
                res = val.get("result", str(val))[:150] if isinstance(val, dict) else str(val)[:150]
                summary.append(f"- {res}")
            return f"Pipeline #{task_id} summary:\n" + "\n".join(summary)

        memories = memory_module.get_all()
        steps = []
        for m in memories:
            if f"Task#{task_id}" in m.get("user", ""):
                steps.append(m)
        if not steps:
            return f"No steps found for task #{task_id}"
        summary = []
        for s in steps:
            summary.append(f"- {s.get('assistant', '')[:150]}")
        return f"Pipeline #{task_id} summary:\n" + "\n".join(summary)

    def _run(self, text, context=None):
        return self.recall(query=text)
