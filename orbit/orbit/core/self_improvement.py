# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Agent Self-Improvement — Feedback Loop Orchestrator

Inspired by Reflexion + Self-Refine + AgentBench:
- Agents receive feedback after each task
- Feedback is scored and categorized
- Improvement plans are generated automatically
- Progress is tracked across iterations
- A/B comparison of old vs new approaches
"""

from .runtime_paths import orbit_data_dir
import json
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Feedback:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    agent_id: str = ""
    task_type: str = ""
    task_input: str = ""
    task_output: str = ""
    score: float = 0.0
    rating: str = "neutral"
    comment: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ImprovementPlan:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    agent_id: str = ""
    goal: str = ""
    strategies: List[str] = field(default_factory=list)
    iterations: int = 0
    best_score: float = 0.0
    current_score: float = 0.0
    history: List[Dict] = field(default_factory=list)
    status: str = "active"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class Lesson:
    """A reusable insight extracted from negative feedback, retrievable by an
    agent at runtime so past failures actually change future behavior."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    agent_id: str = ""
    task_type: str = ""
    trigger: str = ""
    guidance: str = ""
    failure_count: int = 0
    success_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


class SelfImprovement:
    """Orchestrate feedback loops for agent self-improvement."""

    def __init__(self, storage_file: Optional[str] = None):
        self._storage_file = Path(storage_file or str(orbit_data_dir() / "self_improvement.json"))
        self._lock = threading.RLock()
        self._feedback: List[Feedback] = []
        self._plans: Dict[str, ImprovementPlan] = {}
        self._lessons: Dict[str, Lesson] = {}
        self._load()

    def _load(self):
        if self._storage_file.exists():
            try:
                data = json.loads(self._storage_file.read_text(encoding="utf-8"))
                for f in data.get("feedback", []):
                    fb = Feedback(**{k: v for k, v in f.items() if k in Feedback.__dataclass_fields__})
                    self._feedback.append(fb)
                for p in data.get("plans", []):
                    plan = ImprovementPlan(**{k: v for k, v in p.items() if k in ImprovementPlan.__dataclass_fields__})
                    self._plans[plan.id] = plan
                for l in data.get("lessons", []):
                    lesson = Lesson(**{k: v for k, v in l.items() if k in Lesson.__dataclass_fields__})
                    self._lessons[self._lesson_key(lesson.agent_id, lesson.task_type)] = lesson
            except Exception as e:
                logger.error("Failed to load self-improvement data: %s", e)

    def _save(self):
        self._storage_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "feedback": [asdict(f) for f in self._feedback[-2000:]],
            "plans": [asdict(p) for p in self._plans.values()],
            "lessons": [asdict(l) for l in self._lessons.values()],
        }
        self._storage_file.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def add_feedback(
        self, agent_id: str, task_type: str, task_input: str, task_output: str, score: float, comment: str = ""
    ) -> Feedback:
        with self._lock:
            rating = "positive" if score >= 0.7 else "negative" if score <= 0.3 else "neutral"
            fb = Feedback(
                agent_id=agent_id,
                task_type=task_type,
                task_input=task_input[:1000],
                task_output=task_output[:1000],
                score=score,
                rating=rating,
                comment=comment[:500],
            )
            self._feedback.append(fb)
            if len(self._feedback) > 2000:
                self._feedback = self._feedback[-2000:]
            self._auto_improve(agent_id, fb)
            if fb.rating == "negative":
                self._extract_lesson(fb)
            self._save()
            return fb

    def _lesson_key(self, agent_id: str, task_type: str) -> str:
        return f"{agent_id}:{task_type or 'general'}"

    def _extract_lesson(self, feedback: Feedback):
        """Turn a negative feedback into a reusable lesson (deduped per agent+type)."""
        key = self._lesson_key(feedback.agent_id, feedback.task_type)
        trigger = feedback.task_type or (feedback.comment[:40].strip() if feedback.comment else "general")
        guidance = (
            feedback.comment.strip()
            or f"Review and validate output for '{feedback.task_type or 'this task'}' before finishing."
        )
        with self._lock:
            if key in self._lessons:
                lesson = self._lessons[key]
                lesson.failure_count += 1
                lesson.updated_at = datetime.now().isoformat()
                if not lesson.guidance and guidance:
                    lesson.guidance = guidance
            else:
                self._lessons[key] = Lesson(
                    agent_id=feedback.agent_id,
                    task_type=feedback.task_type,
                    trigger=trigger,
                    guidance=guidance,
                    failure_count=1,
                )

    def get_lessons(self, agent_id: str, task_type: str = None) -> List[Dict]:
        with self._lock:
            lessons = [
                l
                for l in self._lessons.values()
                if l.agent_id == agent_id and (task_type is None or l.task_type == task_type)
            ]
            return [asdict(l) for l in sorted(lessons, key=lambda x: x.failure_count, reverse=True)]

    def get_improvement_context(self, agent_id: str, task_type: str = None) -> Dict:
        """Runtime hook: return applicable past lessons so an agent can avoid
        repeating known failures. `prompt_hint` is ready to inject into a prompt."""
        lessons = self.get_lessons(agent_id, task_type)
        ctx: Dict = {"agent_id": agent_id, "task_type": task_type, "lessons": lessons[:5], "prompt_hint": ""}
        if lessons:
            lines = [f"- {l['trigger']}: {l['guidance']}" for l in lessons[:5]]
            ctx["prompt_hint"] = "Based on past feedback, avoid these pitfalls:\n" + "\n".join(lines)
        return ctx

    def analyze_error_patterns(self) -> Dict[str, Dict]:
        """Analyze error patterns across all feedback to find systemic issues."""
        with self._lock:
            error_patterns: Dict[str, Dict] = {}
            for fb in self._feedback:
                if fb.rating != "negative":
                    continue
                # Group by task_type
                key = fb.task_type or "general"
                if key not in error_patterns:
                    error_patterns[key] = {
                        "count": 0,
                        "examples": [],
                        "agents": set(),
                        "common_words": [],
                    }
                pattern = error_patterns[key]
                pattern["count"] += 1
                pattern["agents"].add(fb.agent_id)
                if len(pattern["examples"]) < 3:
                    pattern["examples"].append(
                        {
                            "input": fb.task_input[:100],
                            "output": fb.task_output[:100],
                            "comment": fb.comment[:100],
                        }
                    )

            # Convert sets to lists for JSON
            for key in error_patterns:
                error_patterns[key]["agents"] = list(error_patterns[key]["agents"])

            return error_patterns

    def get_auto_lessons(self, query: str = "") -> List[Dict]:
        """Get lessons automatically based on query patterns (not just agent-specific)."""
        with self._lock:
            relevant = []
            query_lower = query.lower()
            for lesson in self._lessons.values():
                # Check if lesson is relevant to this query
                if lesson.failure_count >= 2:  # Only lessons with repeated failures
                    trigger_lower = lesson.trigger.lower()
                    if any(word in query_lower for word in trigger_lower.split()):
                        relevant.append(asdict(lesson))
            return sorted(relevant, key=lambda x: x["failure_count"], reverse=True)[:5]

    def record_lesson_applied(self, lesson_id: str, success: bool) -> bool:
        """Track whether a retrieved lesson actually helped (closes the loop)."""
        with self._lock:
            for lesson in self._lessons.values():
                if lesson.id == lesson_id:
                    if success:
                        lesson.success_count += 1
                    else:
                        lesson.failure_count += 1
                    lesson.updated_at = datetime.now().isoformat()
                    self._save()
                    return True
            return False

    def _auto_improve(self, agent_id: str, feedback: Feedback):
        agent_plans = [p for p in self._plans.values() if p.agent_id == agent_id and p.status == "active"]
        if not agent_plans:
            return
        for plan in agent_plans:
            plan.iterations += 1
            plan.current_score = feedback.score
            if feedback.score > plan.best_score:
                plan.best_score = feedback.score
            plan.history.append(
                {
                    "iteration": plan.iterations,
                    "score": feedback.score,
                    "comment": feedback.comment[:200],
                    "timestamp": feedback.timestamp,
                }
            )
            if len(plan.history) > 100:
                plan.history = plan.history[-100:]

    def create_plan(self, agent_id: str, goal: str, strategies: List[str] = None) -> ImprovementPlan:
        with self._lock:
            plan = ImprovementPlan(
                agent_id=agent_id,
                goal=goal,
                strategies=strategies
                or [
                    "Review past failures for patterns",
                    "Try alternative approaches",
                    "Break complex tasks into subtasks",
                    "Add validation steps before output",
                ],
            )
            self._plans[plan.id] = plan
            self._save()
            return plan

    def get_agent_feedback(self, agent_id: str, limit: int = 20) -> List[Dict]:
        with self._lock:
            agent_fb = [f for f in self._feedback if f.agent_id == agent_id]
        return [asdict(f) for f in agent_fb[-limit:]]

    def get_agent_score_trend(self, agent_id: str, limit: int = 50) -> Dict:
        with self._lock:
            agent_fb = [f for f in self._feedback if f.agent_id == agent_id]
        recent = agent_fb[-limit:]
        if not recent:
            return {"agent_id": agent_id, "data_points": 0, "avg_score": 0}
        scores = [f.score for f in recent]
        avg = sum(scores) / len(scores)
        recent_avg = sum(scores[-5:]) / min(len(scores), 5) if scores else 0
        return {
            "agent_id": agent_id,
            "data_points": len(recent),
            "avg_score": round(avg, 3),
            "recent_avg": round(recent_avg, 3),
            "trend": "improving" if recent_avg > avg else "declining" if recent_avg < avg else "stable",
            "min_score": round(min(scores), 3),
            "max_score": round(max(scores), 3),
        }

    def get_plan(self, plan_id: str) -> Optional[Dict]:
        with self._lock:
            plan = self._plans.get(plan_id)
            return asdict(plan) if plan else None

    def get_active_plans(self, agent_id: str = "") -> List[Dict]:
        with self._lock:
            plans = list(self._plans.values())
        if agent_id:
            plans = [p for p in plans if p.agent_id == agent_id]
        return [asdict(p) for p in plans if p.status == "active"]

    def close_plan(self, plan_id: str) -> bool:
        with self._lock:
            plan = self._plans.get(plan_id)
            if not plan:
                return False
            plan.status = "completed"
            self._save()
            return True

    def get_stats(self) -> Dict:
        with self._lock:
            feedback = list(self._feedback)
            plans = list(self._plans.values())
            lessons = len(self._lessons)
        if not feedback:
            return {"total_feedback": 0, "total_plans": 0}
        scores = [f.score for f in feedback]
        by_agent = defaultdict(int)
        by_rating = defaultdict(int)
        for f in feedback:
            by_agent[f.agent_id] += 1
            by_rating[f.rating] += 1
        return {
            "total_feedback": len(feedback),
            "total_plans": len(plans),
            "active_plans": sum(1 for p in plans if p.status == "active"),
            "total_lessons": lessons,
            "avg_score": round(sum(scores) / len(scores), 3),
            "by_agent": dict(by_agent),
            "by_rating": dict(by_rating),
        }


# ── Singleton ───────────────────────────────────────────────────────────────

_si: Optional[SelfImprovement] = None
_si_lock = threading.Lock()


def get_self_improvement() -> SelfImprovement:
    global _si
    if _si is None:
        with _si_lock:
            if _si is None:
                _si = SelfImprovement()
    return _si


def submit_feedback(agent_id: str, score: float, **kwargs) -> Feedback:
    return get_self_improvement().add_feedback(agent_id, "", "", "", score, **kwargs)


def get_improvement_stats() -> Dict:
    return get_self_improvement().get_stats()
