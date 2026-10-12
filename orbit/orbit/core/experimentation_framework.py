# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
from .runtime_paths import data_dir
import json
import random
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..logging_config import get_logger

logger = get_logger(__name__)

DATA_DIR = data_dir() / "experimentation"

DEFAULT_TRAFFIC_PCT = 10
MIN_TRIALS_FOR_PROMOTION = 20
SIGNIFICANCE_THRESHOLD = 0.05


class ExperimentationFramework:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._experiments: List[Dict[str, Any]] = []
        self._load()

    def _path(self) -> Path:
        return DATA_DIR / "experiments.json"

    def _load(self):
        path = self._path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._experiments = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load experiments: {e}")

    def _save(self):
        with open(self._path(), "w", encoding="utf-8") as f:
            json.dump(self._experiments, f, indent=2, default=str)

    def create_experiment(
        self,
        name: str,
        task_type: str,
        base_prompt: str,
        traffic_pct: int = DEFAULT_TRAFFIC_PCT,
        variants: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            experiment = {
                "id": f"exp_{uuid.uuid4().hex[:8]}",
                "name": name,
                "task_type": task_type,
                "base_prompt": base_prompt,
                "variants": variants or [],
                "traffic_pct": max(1, min(100, traffic_pct)),
                "status": "draft",
                "winner": None,
                "promoted_at": None,
                "total_trials": 0,
                "control_score": 0.0,
                "variant_scores": {},
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "tags": tags or [],
            }
            self._experiments.append(experiment)
            self._save()
        return experiment

    def get_experiments(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._experiments)
        if status:
            items = [e for e in items if e.get("status") == status]
        items.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return items

    def get_experiment(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        import copy

        with self._lock:
            for e in self._experiments:
                if e["id"] == experiment_id:
                    return copy.deepcopy(e)
        return None

    def activate_experiment(self, experiment_id: str) -> bool:
        with self._lock:
            for e in self._experiments:
                if e["id"] == experiment_id:
                    if not e.get("variants"):
                        return False
                    e["status"] = "active"
                    e["updated_at"] = datetime.now().isoformat()
                    self._save()
                    logger.info(f"Experiment '{e['name']}' activated with {len(e['variants'])} variants")
                    return True
        return False

    def pause_experiment(self, experiment_id: str) -> bool:
        with self._lock:
            for e in self._experiments:
                if e["id"] == experiment_id and e["status"] == "active":
                    e["status"] = "paused"
                    e["updated_at"] = datetime.now().isoformat()
                    self._save()
                    return True
        return False

    def add_variant(self, experiment_id: str, prompt: str, label: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            for e in self._experiments:
                if e["id"] == experiment_id:
                    variant = {
                        "id": f"var_{uuid.uuid4().hex[:8]}",
                        "prompt": prompt,
                        "label": label or f"Variant {len(e['variants']) + 1}",
                        "trials": 0,
                        "avg_score": 0.0,
                        "avg_latency": 0.0,
                    }
                    e["variants"].append(variant)
                    e["updated_at"] = datetime.now().isoformat()
                    self._save()
                    return variant
        return None

    def generate_variants_llm(self, experiment_id: str, count: int = 3) -> List[Dict[str, Any]]:
        exp = self.get_experiment(experiment_id)
        if not exp:
            return []
        base = exp.get("base_prompt", "")
        task_type = exp.get("task_type", "general")
        generated = []
        for i in range(count):
            variant_prompt = self._mutate_prompt(base, task_type, i)
            var = self.add_variant(experiment_id, variant_prompt, f"LLM-Gen {i + 1}")
            if var:
                generated.append(var)
        return generated

    def _mutate_prompt(self, base_prompt: str, task_type: str, seed: int) -> str:
        mutations = [
            f"{base_prompt}\n\nBe thorough and provide concrete examples.",
            f"{base_prompt}\n\nFocus on accuracy and cite specific data points.",
            f"{base_prompt}\n\nThink step by step and explain your reasoning.",
            f"{base_prompt}\n\nProvide multiple perspectives and weigh trade-offs.",
            f"Analyze the following with deep expertise: {base_prompt}",
            f"Consider edge cases and potential pitfalls: {base_prompt}\n\nThen provide a balanced answer.",
            f"{base_prompt}\n\nStructure your response with clear sections and a summary.",
            f"{base_prompt}\n\nPrioritize actionable insights over theoretical discussion.",
            f"Before answering, list the key assumptions: {base_prompt}",
            f"{base_prompt}\n\nIf uncertain, state your confidence level explicitly.",
        ]
        return mutations[seed % len(mutations)]

    def route_prompt(self, task_type: str, prompt: str) -> Tuple[str, Optional[str], Optional[str]]:
        with self._lock:
            active = [e for e in self._experiments if e.get("status") == "active" and e.get("task_type") == task_type]
            if not active:
                return prompt, None, None
            experiment = active[0]
            if not experiment.get("variants"):
                return prompt, None, None
            if random.randint(1, 100) > experiment.get("traffic_pct", DEFAULT_TRAFFIC_PCT):
                return prompt, experiment["id"], "control"
            variant = random.choice(experiment["variants"])
            return variant["prompt"], experiment["id"], variant["id"]

    def record_result(
        self, experiment_id: str, variant_id: str, score: float, latency_ms: float = 0, success: bool = True
    ) -> bool:
        with self._lock:
            for e in self._experiments:
                if e["id"] != experiment_id:
                    continue
                e["total_trials"] = e.get("total_trials", 0) + 1
                e["updated_at"] = datetime.now().isoformat()
                if variant_id == "control":
                    prev_total = e.get("control_score", 0) * e.get("control_trials", 0)
                    e["control_trials"] = e.get("control_trials", 0) + 1
                    e["control_score"] = round((prev_total + score) / e["control_trials"], 4)
                    self._check_promotion(e)
                    self._save()
                    return True
                for v in e["variants"]:
                    if v["id"] == variant_id:
                        prev_total = v.get("avg_score", 0) * v.get("trials", 0)
                        v["trials"] = v.get("trials", 0) + 1
                        v["avg_score"] = round((prev_total + score) / v["trials"], 4)
                        v["avg_latency"] = (
                            round((v.get("avg_latency", 0) * (v["trials"] - 1) + latency_ms) / v["trials"], 1)
                            if v["trials"] > 1
                            else latency_ms
                        )
                        e["variant_scores"][variant_id] = v["avg_score"]
                        self._check_promotion(e)
                        self._save()
                        return True
        return False

    def _check_promotion(self, experiment: Dict[str, Any]):
        if experiment.get("status") != "active":
            return
        control_trials = experiment.get("control_trials", 0)
        control_score = experiment.get("control_score", 0)
        if control_trials < MIN_TRIALS_FOR_PROMOTION:
            return
        best_var = None
        best_score = control_score
        for v in experiment.get("variants", []):
            if v.get("trials", 0) < MIN_TRIALS_FOR_PROMOTION:
                continue
            var_score = v.get("avg_score", 0)
            if var_score > best_score and var_score - control_score > SIGNIFICANCE_THRESHOLD:
                best_score = var_score
                best_var = v
        if best_var:
            experiment["winner"] = best_var["id"]
            experiment["status"] = "promoted"
            experiment["promoted_at"] = datetime.now().isoformat()
            logger.info(
                f"Experiment '{experiment['name']}' promoted variant {best_var.get('label')} "
                f"(score {best_score:.4f} vs control {control_score:.4f})"
            )

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            active = [e for e in self._experiments if e.get("status") == "active"]
            promoted = [e for e in self._experiments if e.get("status") == "promoted"]
            total_variants = sum(len(e.get("variants", [])) for e in self._experiments)
            total_trials = sum(e.get("total_trials", 0) for e in self._experiments)
            return {
                "total_experiments": len(self._experiments),
                "active_experiments": len(active),
                "promoted_experiments": len(promoted),
                "total_variants": total_variants,
                "total_trials": total_trials,
                "unique_task_types": len(set(e.get("task_type", "") for e in self._experiments)),
            }

    def delete_experiment(self, experiment_id: str) -> bool:
        with self._lock:
            before = len(self._experiments)
            self._experiments = [e for e in self._experiments if e["id"] != experiment_id]
            if len(self._experiments) < before:
                self._save()
                return True
        return False

    def reset(self):
        with self._lock:
            self._experiments = []
            self._save()


_instance: Optional[ExperimentationFramework] = None
_singleton_lock = threading.Lock()


def get_experimentation_framework() -> ExperimentationFramework:
    global _instance
    if _instance is None:
        with _singleton_lock:
            if _instance is None:
                _instance = ExperimentationFramework()
    return _instance
