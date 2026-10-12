# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Decision Engine — Multi-criteria decision analysis

Helps Priyanshu make better decisions with weighted scoring,
risk assessment, and Tony Stark's personality filter.

What would Tony Stark do? He'd probably build a suit.
But first, he'd analyze the options.
"""

from .runtime_paths import data_dir
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..logging_config import get_logger

logger = get_logger(__name__)

DATA_DIR = data_dir() / "decisions"


PERSONALITY_FILTERS = {
    "tony_stark": {
        "name": "Tony Stark",
        "approach": "Bold innovation, calculated risks, building the future",
        "traits": ["innovative", "risk-taker", "efficiency-focused", "visionary"],
        "prefix": "What would Tony Stark do?",
    },
    "pepper_potts": {
        "name": "Pepper Potts",
        "approach": "Strategic planning, risk management, sustainable growth",
        "traits": ["pragmatic", "organized", "long-term thinker", "diplomatic"],
        "prefix": "What would Pepper Potts advise?",
    },
    "james_howlett": {
        "name": "James Howlett (Wolverine)",
        "approach": "Direct action, loyalty-driven, no-nonsense",
        "traits": ["direct", "fierce", "loyal", "gut-driven"],
        "prefix": "What would Wolverine do?",
    },
}


class DecisionEngine:
    CATEGORIES = ["technical", "personal", "business", "creative"]
    RISK_LEVELS = ["LOW", "MEDIUM", "HIGH"]

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._decisions: List[Dict] = []
        self._load()

    def _path(self) -> Path:
        return DATA_DIR / "decisions.json"

    def _load(self):
        p = self._path()
        if p.exists():
            try:
                self._decisions = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                self._decisions = []

    def _save(self):
        p = self._path()
        p.write_text(json.dumps(self._decisions[-500:], indent=2, ensure_ascii=False), encoding="utf-8")

    def analyze(
        self,
        question: str,
        options: List[Dict],
        category: str = "personal",
        criteria: List[Dict] = None,
        personality: str = "tony_stark",
    ) -> Dict:
        decision_id = str(uuid.uuid4())[:12]
        start = time.time()

        if not criteria:
            criteria = [
                {"name": "Impact", "weight": 0.3},
                {"name": "Feasibility", "weight": 0.25},
                {"name": "Risk", "weight": 0.2},
                {"name": "Cost", "weight": 0.15},
                {"name": "Time", "weight": 0.1},
            ]

        scored_options = []
        missing_scores = False
        for opt in options:
            option_scores = {}
            total_weighted = 0
            opt_scores = opt.get("scores", {}) if isinstance(opt.get("scores"), dict) else {}
            for c in criteria:
                raw = opt_scores.get(c["name"])
                if raw is None:
                    missing_scores = True
                    score = 0.0
                else:
                    try:
                        score = float(raw)
                    except (TypeError, ValueError):
                        missing_scores = True
                        score = 0.0
                option_scores[c["name"]] = round(score, 1)
                total_weighted += score * c["weight"]
            risk = self._assess_risk(opt, criteria)
            scored_options.append(
                {
                    "name": opt.get("name", "Unknown"),
                    "scores": option_scores,
                    "weighted_total": round(total_weighted, 2),
                    "risk": risk,
                    "pros": opt.get("pros", []),
                    "cons": opt.get("cons", []),
                }
            )

        scored_options.sort(key=lambda x: x["weighted_total"], reverse=True)
        winner = scored_options[0] if scored_options else None

        personality_data = PERSONALITY_FILTERS.get(personality, PERSONALITY_FILTERS["tony_stark"])

        decision = {
            "id": decision_id,
            "question": question,
            "category": category,
            "options": scored_options,
            "winner": winner["name"] if winner else None,
            "winner_score": winner["weighted_total"] if winner else 0,
            "confidence": (0 if missing_scores else min(winner["weighted_total"], 95)) if winner else 0,
            "criteria": criteria,
            "personality": personality_data,
            "recommendation": self._generate_recommendation(question, winner, personality_data),
            "created_at": datetime.now().isoformat(),
            "elapsed_ms": round((time.time() - start) * 1000, 1),
        }

        with self._lock:
            self._decisions.append(decision)
            self._save()

        return decision

    def _assess_risk(self, option: Dict, criteria: List[Dict]) -> str:
        risk_score = option.get("scores", {}).get("Risk", 50)
        if risk_score >= 70:
            return "HIGH"
        elif risk_score >= 40:
            return "MEDIUM"
        return "LOW"

    def _generate_recommendation(self, question: str, winner: Dict, personality: Dict) -> str:
        if not winner:
            return "Insufficient data for recommendation."
        name = winner["name"]
        score = winner["weighted_total"]
        risk = winner["risk"]
        prefix = personality.get("prefix", "Consider this:")
        approach = personality.get("approach", "")
        return f"{prefix} Based on {personality['name']}'s approach ({approach}), '{name}' wins with {score:.1f}/100 weighted score. Risk level: {risk}."

    def get_history(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            return list(reversed(self._decisions[-limit:]))

    def get_decision(self, decision_id: str) -> Optional[Dict]:
        with self._lock:
            for d in self._decisions:
                if d.get("id") == decision_id:
                    return d
        return None

    def get_stats(self) -> Dict:
        with self._lock:
            total = len(self._decisions)
            if total == 0:
                return {"total": 0, "categories": {}, "avg_confidence": 0}
            categories = {}
            conf_sum = 0
            for d in self._decisions:
                cat = d.get("category", "other")
                categories[cat] = categories.get(cat, 0) + 1
                conf_sum += d.get("confidence", 0)
            return {
                "total": total,
                "categories": categories,
                "avg_confidence": round(conf_sum / total, 1),
            }


_instance: Optional[DecisionEngine] = None
_singleton_lock = threading.Lock()


def get_decision_engine() -> DecisionEngine:
    global _instance
    if _instance is None:
        with _singleton_lock:
            if _instance is None:
                _instance = DecisionEngine()
    return _instance
