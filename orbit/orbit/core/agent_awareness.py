# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Agent Awareness — Self-awareness for every agent

Each agent maintains a journal of its decisions, tracks accuracy per domain,
and can determine whether to handle a task solo or delegate.
"""

from .runtime_paths import data_dir
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..logging_config import get_logger

logger = get_logger(__name__)

DATA_DIR = data_dir() / "agent_awareness"


class Decision:
    def __init__(
        self,
        domain: str,
        task: str,
        prediction: str,
        actual_outcome: str,
        correct: bool,
        confidence_score: float = 0.0,
        execution_time: float = 0.0,
        tokens_used: int = 0,
        cost: float = 0.0,
    ):
        self.timestamp = datetime.now()
        self.domain = domain
        self.task = task
        self.prediction = prediction
        self.actual_outcome = actual_outcome
        self.correct = correct
        self.confidence_score = confidence_score
        self.execution_time = execution_time
        self.tokens_used = tokens_used
        self.cost = cost

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "domain": self.domain,
            "task": self.task,
            "prediction": self.prediction,
            "actual_outcome": self.actual_outcome,
            "correct": self.correct,
            "confidence_score": self.confidence_score,
            "execution_time": self.execution_time,
            "tokens_used": self.tokens_used,
            "cost": self.cost,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Decision":
        dec = cls(
            domain=d["domain"],
            task=d["task"],
            prediction=d["prediction"],
            actual_outcome=d["actual_outcome"],
            correct=d["correct"],
            confidence_score=d.get("confidence_score", 0.0),
            execution_time=d.get("execution_time", 0.0),
            tokens_used=d.get("tokens_used", 0),
            cost=d.get("cost", 0.0),
        )
        dec.timestamp = datetime.fromisoformat(d["timestamp"])
        return dec


class AgentPerformanceJournal:
    def __init__(self, agent_name: str, domains: List[str]):
        self.agent_name = agent_name
        self.domains = domains
        self.decisions: List[Decision] = []
        self.accuracy_per_domain: Dict[str, float] = {}
        self.confidence_per_domain: Dict[str, float] = {}
        self.total_decisions_per_domain: Dict[str, int] = {}
        self.failure_patterns: List[Dict] = []

    def log_decision(self, decision: Decision) -> None:
        self.decisions.append(decision)
        domain = decision.domain

        if domain not in self.total_decisions_per_domain:
            self.total_decisions_per_domain[domain] = 0
            self.accuracy_per_domain[domain] = 0.0

        n = self.total_decisions_per_domain[domain]
        self.total_decisions_per_domain[domain] = n + 1
        current = self.accuracy_per_domain[domain]
        new_acc = (current * n + (1.0 if decision.correct else 0.0)) / (n + 1)
        self.accuracy_per_domain[domain] = round(new_acc, 4)
        self.confidence_per_domain[domain] = round(self.accuracy_per_domain[domain] * 100, 1)

        if not decision.correct:
            self._analyze_failure(decision)

    def should_handle_task(self, domain: str, task_complexity: float = 0.5) -> bool:
        if domain not in self.accuracy_per_domain:
            return False
        accuracy = self.accuracy_per_domain[domain]
        if task_complexity > 0.7 and accuracy < 0.75:
            return False
        return True

    def get_confidence(self, domain: str) -> float:
        if domain not in self.confidence_per_domain:
            return 50.0
        return self.confidence_per_domain[domain]

    def get_weak_domains(self, threshold: float = 0.6) -> List[str]:
        return [d for d, acc in self.accuracy_per_domain.items() if acc < threshold]

    def get_strong_domains(self, threshold: float = 0.8) -> List[str]:
        return [d for d, acc in self.accuracy_per_domain.items() if acc >= threshold]

    def _analyze_failure(self, decision: Decision) -> None:
        MAX_FAILURE_PATTERNS = 100
        self.failure_patterns.append(
            {
                "timestamp": decision.timestamp.isoformat(),
                "domain": decision.domain,
                "task": decision.task,
                "prediction": decision.prediction,
                "actual": decision.actual_outcome,
                "confidence_was": decision.confidence_score,
            }
        )
        if len(self.failure_patterns) > MAX_FAILURE_PATTERNS:
            self.failure_patterns = self.failure_patterns[-MAX_FAILURE_PATTERNS:]

    def get_performance_summary(self) -> Dict:
        correct = sum(1 for d in self.decisions if d.correct)
        total = len(self.decisions)
        return {
            "agent": self.agent_name,
            "domains": self.domains,
            "total_decisions": total,
            "overall_accuracy": round(correct / total, 3) if total else 0.0,
            "accuracy_per_domain": {d: round(acc, 3) for d, acc in self.accuracy_per_domain.items()},
            "confidence_per_domain": {d: round(self.get_confidence(d), 1) for d in self.domains},
            "total_decisions_per_domain": dict(self.total_decisions_per_domain),
            "failure_count": sum(1 for d in self.decisions if not d.correct),
            "failure_patterns": len(self.failure_patterns),
            "failure_examples": self.failure_patterns[-5:],
            "weak_domains": self.get_weak_domains(),
            "strong_domains": self.get_strong_domains(),
            "avg_execution_time": (round(sum(d.execution_time for d in self.decisions) / total, 3) if total else 0),
        }

    def to_dict(self) -> Dict:
        return {
            "agent_name": self.agent_name,
            "domains": self.domains,
            "decisions": [d.to_dict() for d in self.decisions],
            "accuracy_per_domain": self.accuracy_per_domain,
            "confidence_per_domain": self.confidence_per_domain,
            "total_decisions_per_domain": self.total_decisions_per_domain,
            "failure_patterns": self.failure_patterns,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "AgentPerformanceJournal":
        journal = cls(d["agent_name"], d.get("domains", []))
        journal.decisions = [Decision.from_dict(dd) for dd in d.get("decisions", [])]
        journal.accuracy_per_domain = {k: float(v) for k, v in d.get("accuracy_per_domain", {}).items()}
        journal.confidence_per_domain = {k: float(v) for k, v in d.get("confidence_per_domain", {}).items()}
        journal.total_decisions_per_domain = {k: int(v) for k, v in d.get("total_decisions_per_domain", {}).items()}
        journal.failure_patterns = list(d.get("failure_patterns", []))
        return journal


class AgentAwarenessManager:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.journals: Dict[str, AgentPerformanceJournal] = {}
        self.lock = threading.Lock()
        self._load()

    def _data_path(self) -> Path:
        return DATA_DIR / "journals.json"

    def _load(self):
        path = self._data_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for name, jdata in data.items():
                        self.journals[name] = AgentPerformanceJournal.from_dict(jdata)
            except (json.JSONDecodeError, OSError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"Failed to load agent journals: {e}")

    def _save(self):
        path = self._data_path()
        data = {name: j.to_dict() for name, j in self.journals.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def register_agent(self, agent_name: str, domains: List[str]) -> None:
        with self.lock:
            if agent_name not in self.journals:
                self.journals[agent_name] = AgentPerformanceJournal(agent_name, domains)
                self._save()

    def log_decision(self, agent_name: str, decision: Decision) -> None:
        with self.lock:
            if agent_name in self.journals:
                self.journals[agent_name].log_decision(decision)
                self._save()

    def get_best_agent_for_domain(self, domain: str) -> Optional[str]:
        with self.lock:
            snap = [(name, list(j.domains), j.confidence_per_domain.get(domain, 50.0)) for name, j in self.journals.items()]
        ranked = [(name, conf) for name, domains, conf in snap if domain in domains]
        if not ranked:
            return None
        return max(ranked, key=lambda x: x[1])[0]

    def get_team_summary(self) -> Dict:
        with self.lock:
            snap = {name: j.to_dict() for name, j in self.journals.items()}
        all_decisions = [d for j in snap.values() for d in j.get("decisions", [])]
        agents = {}
        for name, j in snap.items():
            decs = j.get("decisions", [])
            correct = sum(1 for d in decs if d.get("correct"))
            total = len(decs)
            agents[name] = {
                "agent": j.get("agent_name", name),
                "domains": j.get("domains", []),
                "total_decisions": total,
                "overall_accuracy": round(correct / total, 3) if total else 0.0,
                "accuracy_per_domain": dict(j.get("accuracy_per_domain", {})),
                "confidence_per_domain": dict(j.get("confidence_per_domain", {})),
                "total_decisions_per_domain": dict(j.get("total_decisions_per_domain", {})),
                "failure_count": sum(1 for d in decs if not d.get("correct")),
                "failure_patterns": len(j.get("failure_patterns", [])),
                "failure_examples": j.get("failure_patterns", [])[-5:],
            }
        return {
            "agents": agents,
            "team_size": len(snap),
            "total_decisions": len(all_decisions),
            "total_failures": sum(1 for d in all_decisions if not d.get("correct")),
        }

    def get_journal(self, agent_name: str) -> Optional[AgentPerformanceJournal]:
        with self.lock:
            return self.journals.get(agent_name)

    def get_all_journals(self) -> Dict[str, AgentPerformanceJournal]:
        with self.lock:
            return dict(self.journals)

    def reset(self):
        with self.lock:
            self.journals = {}
            self._save()


_instance: Optional[AgentAwarenessManager] = None
_singleton_lock = threading.Lock()


def get_awareness_manager() -> AgentAwarenessManager:
    global _instance
    if _instance is None:
        with _singleton_lock:
            if _instance is None:
                _instance = AgentAwarenessManager()
    return _instance
