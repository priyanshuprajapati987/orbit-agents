# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Competitive Optimizer — data-driven strategy engine.

Consumes a saved battle log (the `{category: {results, win_rate, ...}}` shape
from `core.battle`) and produces per-category optimization recommendations:

    win_rate < 0.40  -> MAJOR   (re-route, force CoT, add retrieval)
    0.40 - 0.60      -> TARGETED (prompt/model A/B tweaks)
    0.60+            -> MAINTENANCE (keep strategy, minor polish)

Also exposes the raw weak-prompt list (lowest composite rounds) so the team can
drill into exactly which queries are being lost.

Usage:
    from .optimization import generate_optimization_report
    report = generate_optimization_report(battle_payload)
"""

import threading
from typing import Any, Dict, List, Optional

_CATEGORY_TO_AGENT = {
    "reasoning": "DEC",
    "coding": "COD",
    "creative": "WRI",
    "facts": "RES",
    "math": "DEC",
    "general": "QUEEN",
    "multi_turn": "MEM",
}

# Win-rate thresholds (inclusive bounds as documented above).
MAJOR_THRESHOLD = 0.40
TARGETED_THRESHOLD = 0.60

# Optimization playbooks per category. `actions` are concrete steps, `routes`
# are preferred provider/model chains to try first for that category.
PLAYBOOKS: Dict[str, Dict[str, Any]] = {
    "reasoning": {
        "actions": [
            "Force multi-step CoT via REASONING_PROMPT for all reasoning queries",
            "Route to deepseek-r1 (OpenRouter) as the primary reasoning model",
            "Add 'show step-by-step' phrasing when prompt is ambiguous",
            "Fall back to qwen2.5:14b locally if OpenRouter is degraded",
        ],
        "routes": [["openrouter", "deepseek/deepseek-r1"], ["groq", "llama-3.3-70b-versatile"], ["ollama", "qwen2.5"]],
    },
    "coding": {
        "actions": [
            "Route to qwen3-coder (OpenRouter) as the primary coder",
            "Pass explicit language/constraints when inferred from the prompt",
            "Add edge-case + test suggestion framing to CODING_PROMPT",
            "Fall back to groq deepseek-coder if OpenRouter is degraded",
        ],
        "routes": [
            ["openrouter", "qwen/qwen3-coder"],
            ["groq", "deepseek-r1-distill-llama-70b"],
            ["ollama", "qwen2.5-coder"],
        ],
    },
    "creative": {
        "actions": [
            "Route to gemini-2.0-flash-exp for tone/creativity",
            "Use CREATIVE_PROMPT with explicit form/length constraints",
            "A/B test temperature 0.9 vs 0.7 for creative output",
        ],
        "routes": [["gemini", "gemini-2.0-flash-exp"], ["openrouter", "openai/gpt-4o-mini"], ["ollama", "qwen2.5"]],
    },
    "facts": {
        "actions": [
            "Route factual queries to gemini-2.0-flash-exp (knowledge)",
            "Pre-seed the fact cache with common stable answers",
            "Add 'answer directly and concisely' framing (FACTS_PROMPT)",
        ],
        "routes": [
            ["gemini", "gemini-2.0-flash-exp"],
            ["openrouter", "openai/gpt-4o-mini"],
            ["groq", "llama-3.3-70b-versatile"],
        ],
    },
    "search": {
        "actions": [
            "Route factual lookups to gemini-2.0-flash-exp",
            "Enable fact-cache seeding for stable trivia answers",
        ],
        "routes": [
            ["gemini", "gemini-2.0-flash-exp"],
            ["openrouter", "openai/gpt-4o-mini"],
            ["groq", "llama-3.3-70b-versatile"],
        ],
    },
    "math": {
        "actions": [
            "Force step-by-step working; require a final numeric answer line",
            "Route to deepseek-r1 / qwen2.5:14b for reliable arithmetic",
        ],
        "routes": [["openrouter", "deepseek/deepseek-r1"], ["ollama", "qwen2.5"]],
    },
    "multi_turn": {
        "actions": [
            "Boost context_manager summary frequency for long conversations",
            "Keep live window >= 12 turns; verify turn-history is injected",
            "A/B test context budget ratios (0.5/0.3/0.2)",
        ],
        "routes": [],
    },
    "general": {
        "actions": [
            "Verify the chain has at least one reachable provider",
            "Tune the fallback chain order per observed latency",
        ],
        "routes": [
            ["gemini", "gemini-2.0-flash-exp"],
            ["openrouter", "openai/gpt-4o-mini"],
            ["groq", "llama-3.3-70b-versatile"],
        ],
    },
}

DEFAULT_PLAYBOOK: Dict[str, Any] = {
    "actions": [
        "Review per-prompt composite scores and target the weakest clusters",
        "Re-run the battle after any routing/prompt change to measure impact",
    ],
    "routes": [],
}


class CompetitiveOptimizer:
    """Analyze battle results and recommend concrete optimizations."""

    def classify(self, win_rate: float) -> str:
        if win_rate < MAJOR_THRESHOLD:
            return "MAJOR"
        if win_rate < TARGETED_THRESHOLD:
            return "TARGETED"
        return "MAINTENANCE"

    def recommend(self, category: str, win_rate: float) -> Dict[str, Any]:
        """Strategy for a single category based on its observed win rate."""
        playbook = PLAYBOOKS.get(category.lower(), DEFAULT_PLAYBOOK)
        return {
            "category": category,
            "win_rate": round(win_rate, 4),
            "severity": self.classify(win_rate),
            "actions": list(playbook.get("actions", [])),
            "suggested_routes": playbook.get("routes", []),
            "message": self._message(self.classify(win_rate)),
        }

    @staticmethod
    def _message(severity: str) -> str:
        if severity == "MAJOR":
            return "Category is a major weakness — re-route, add CoT/retrieval, then re-battle."
        if severity == "TARGETED":
            return "Category is competitive — A/B test prompt/model variants to push past 60%."
        return "Category is strong — keep the strategy, apply minor polish only."

    def analyze_battle(self, all_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Per-category analysis from a battle-log `categories` dict."""
        rows: List[Dict[str, Any]] = []
        for category, data in sorted(all_results.items()):
            results = data.get("results", [])
            valid = [r for r in results if not (r.get("opponent_error") or r.get("jarvus_error"))]
            wins = sum(1 for r in valid if r.get("composite", {}).get("winner") == "jarvus")
            n = len(valid)
            rate = wins / n if n else 0.0
            latencies = [r.get("jarvus", {}).get("latency_ms", 0) for r in valid]
            rows.append(
                {
                    "category": category,
                    "prompts": len(results),
                    "valid": n,
                    "voids": len(results) - n,
                    "wins": wins,
                    "win_rate": round(rate, 4),
                    "severity": self.classify(rate),
                    "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
                }
            )
        return rows

    def weak_prompts(self, all_results: Dict[str, Any], top: int = 10) -> List[Dict[str, Any]]:
        """The lowest-composite complete rounds across all categories."""
        rounds: List[Dict[str, Any]] = []
        for category, data in all_results.items():
            for r in data.get("results", []):
                if r.get("opponent_error") or "composite" not in r:
                    continue
                rounds.append(
                    {
                        "category": category,
                        "prompt": r.get("prompt"),
                        "winner": r.get("composite", {}).get("winner"),
                        "composite": r.get("composite", {}).get("total", 0.0),
                        "jarvus_latency_ms": r.get("jarvus", {}).get("latency_ms", 0.0),
                        "accuracy_score": r.get("accuracy_score", 0.0),
                        "reasoning": r.get("reasoning", {}),
                    }
                )
        rounds.sort(key=lambda r: r["composite"])
        return rounds[:top]

    def generate_report(self, battle_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Full optimization report from a saved battle payload."""
        categories = battle_payload.get("categories", battle_payload)
        if isinstance(categories, dict) and "summary" in battle_payload and "categories" in battle_payload:
            categories = battle_payload["categories"]
        summary = battle_payload.get("summary", {})

        rows = self.analyze_battle(categories)
        overall_rate = summary.get("overall_win_rate")
        if overall_rate is None and rows:
            overall_rate = (
                sum(r["wins"] for r in rows) / sum(r["valid"] for r in rows) if sum(r["valid"] for r in rows) else 0.0
            )

        recommendations = []
        for row in rows:
            rec = self.recommend(row["category"], row["win_rate"])
            recommendations.append({**row, "actions": rec["actions"], "suggested_routes": rec["suggested_routes"]})

        overall_severity = self.classify(overall_rate or 0.0)
        return {
            "report_date": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
            "opponent": summary.get("opponent"),
            "overall_win_rate": round(overall_rate, 4) if overall_rate is not None else 0.0,
            "overall_severity": overall_severity,
            "total_wins": sum(r["wins"] for r in rows),
            "total_complete": sum(r["valid"] for r in rows),
            "categories": recommendations,
            "weak_prompts": self.weak_prompts(categories, top=10),
            "verdict": (
                "Competitive position healthy — focus on the TARGETED/MAJOR categories."
                if overall_severity != "MAJOR"
                else "Competitive position weak — execute the MAJOR actions before re-battling."
            ),
        }

    def feed_battle_to_router(self, battle_payload: Dict[str, Any]) -> int:
        """Feed battle results back into SmartRouter training data.

        For each lost round, records the query pattern as a correction
        so the router learns from mistakes. Returns number of corrections fed.
        """
        from .smart_router import record_routing_feedback

        categories = battle_payload.get("categories", battle_payload)
        if isinstance(categories, dict) and "summary" in battle_payload:
            categories = battle_payload["categories"]
        fed = 0
        for category, data in categories.items():
            for r in data.get("results", []):
                winner = r.get("composite", {}).get("winner")
                prompt = r.get("prompt", "")
                if not prompt or winner == "void":
                    continue
                if winner == "opponent":
                    # We lost — tell the router this query pattern was wrong for current routing
                    agent_label = _CATEGORY_TO_AGENT.get(category, "QUEEN")
                    record_routing_feedback(agent_label, prompt, correct=False)
                    fed += 1
                elif winner == "jarvus":
                    # We won — reinforce the routing
                    agent_label = _CATEGORY_TO_AGENT.get(category, "QUEEN")
                    record_routing_feedback(agent_label, prompt, correct=True)
                    fed += 1
        return fed


_optimizer: Optional[CompetitiveOptimizer] = None
_optimizer_lock = threading.Lock()


def get_optimizer() -> CompetitiveOptimizer:
    global _optimizer
    if _optimizer is None:
        with _optimizer_lock:
            if _optimizer is None:
                _optimizer = CompetitiveOptimizer()
    return _optimizer


def generate_optimization_report(battle_payload: Dict[str, Any]) -> Dict[str, Any]:
    return get_optimizer().generate_report(battle_payload)
