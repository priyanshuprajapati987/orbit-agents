# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import json
import threading
import time

from ..core.llm import legacy_adapter as llm_router
from ..config import ORBIT_SYSTEM_PROMPT
from .critic import CriticAgent
from .executor_agent import ExecutorAgent
from .memory_agent import PipelineMemoryAgent
from .planner import PlannerAgent


class PipelineOrchestrator:
    def __init__(self, assistant=None):
        self.planner = PlannerAgent(assistant)
        self.executor = ExecutorAgent(assistant)
        self.memory = PipelineMemoryAgent(assistant)
        self.critic = CriticAgent(assistant)
        self.assistant = assistant
        self._results = {}
        self._lock = threading.Lock()
        self._on_status = None

    def set_callback(self, on_status=None):
        self._on_status = on_status

    def _emit(self, stage, msg):
        if self._on_status:
            self._on_status(stage, msg)

    def run(self, task: str, task_id: int = None) -> dict:
        if task_id is None:
            task_id = int(time.time() * 1000) % 100000

        steps = []
        start_time = time.time()
        with self._lock:
            self._results.clear()
            self._current_task_id = task_id

        self._emit("planning", "Creating execution plan...")
        plan = self.planner.execute(task)
        try:
            plan_dict = json.loads(plan) if isinstance(plan, str) else plan
        except Exception:
            plan_dict = {"subtasks": [{"id": 1, "type": "ask", "description": task}], "parallel": False}

        subtasks = plan_dict.get("subtasks", [])
        parallel = plan_dict.get("parallel", False)

        if not subtasks:
            subtasks = [{"id": 1, "type": "memory", "description": task, "dependencies": []}]

        context = ""

        def run_subtask(subtask):
            sid = subtask.get("id", 1)
            desc = subtask.get("description", "")
            stype = subtask.get("type", "memory")
            attempt = 0
            last_result = ""

            while attempt <= self.critic.MAX_RETRIES:
                self._emit("executing", f"Subtask {sid}: {stype}")
                result = self.executor.execute_subtask(subtask, context)

                self._emit("reviewing", f"Reviewing subtask {sid}...")
                verdict = self.critic.review(subtask, result, attempt)

                quality = verdict.get("quality", "fail")
                score = verdict.get("score", 0.0)
                feedback = verdict.get("feedback", "")
                suggested_fix = verdict.get("suggested_fix", "")

                step_result = {
                    "id": sid,
                    "type": stype,
                    "description": desc,
                    "result": (result or "")[:500],
                    "quality": quality,
                    "score": score,
                    "feedback": feedback,
                    "attempt": attempt + 1,
                }

                if quality == "pass" or not self.critic.needs_retry(verdict, attempt):
                    last_result = result
                    with self._lock:
                        self._results[sid] = step_result
                    break

                attempt += 1
                if suggested_fix:
                    self._emit("retrying", f"Retry {attempt}: {suggested_fix}")
                    subtask["description"] = f"{desc} (fix: {suggested_fix})"

            return last_result, step_result

        if parallel:
            threads = []
            lock = threading.Lock()
            context_parts = []

            def worker(st):
                res, step = run_subtask(st)
                with lock:
                    context_parts.append(f"\nSubtask {st['id']}: {(res or '')[:200]}")

            for st in subtasks:
                import copy

                st_copy = copy.deepcopy(st)
                t = threading.Thread(target=worker, args=(st_copy,), daemon=True)
                t.start()
                threads.append(t)

            for t in threads:
                t.join()
            context = "".join(context_parts)
        else:
            for st in subtasks:
                res, step = run_subtask(st)
                context += f"\nSubtask {st['id']}: {(res or '')[:200]}"

        with self._lock:
            for sid, step in sorted(self._results.items()):
                if step not in steps:
                    steps.append(step)

        self._emit("saving", "Saving to memory...")
        self.memory.save_result(task_id, 0, context, task)

        self._emit("synthesizing", "Synthesizing results...")
        synthesis = self._synthesize(task, steps)

        elapsed = round(time.time() - start_time, 1)

        return {
            "task_id": task_id,
            "task": task,
            "status": "completed",
            "steps": steps,
            "result": synthesis,
            "elapsed_seconds": elapsed,
        }

    def _synthesize(self, task: str, steps: list) -> str:
        if not steps:
            return "No steps executed."

        parts = []
        for s in steps:
            if s.get("quality") == "pass":
                parts.append(f"Step {s['id']} ({s['type']}): {s['result'][:300]}")

        if not parts:
            return "All steps failed. Try rephrasing the task."

        prompt = f"""{ORBIT_SYSTEM_PROMPT}
Synthesize these execution results into a clear response for the user.

Original task: {task}

Results:
{chr(10).join(parts)}

Provide a concise summary combining all results naturally."""
        try:
            synthesis = llm_router.generate(prompt, force_offline=True)
            return synthesis or "\n".join(parts)
        except Exception:
            return "\n".join(parts)

    def get_result(self, task_id: int) -> dict:
        with self._lock:
            if getattr(self, "_current_task_id", None) == task_id:
                return dict(self._results)
            return {}

    def clear_results(self):
        with self._lock:
            self._results = {}
