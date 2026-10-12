# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Reasoning Depth Scorer — CoT quality vs ChatGPT baseline.

Heuristic scoring of chain-of-thought quality: step structure, reasoning
connectors, math step decomposition, and hedging. Produces a 0-100 depth
score comparable across responses.

Two layers:
  - `score_depth` / `compare_depth`  — fast, pure-regex (no dependencies)
  - `ReasoningDepthAnalyzer`         — sentence-level analysis with richer
    metrics (self-correction, step clarity, uncertainty). NLTK is optional;
    falls back to regex sentence splitting when not installed.

Usage:
    from ..core.reasoning_depth import score_depth, compare_depth, analyze_reasoning
    score = score_depth("First, compute X. Then, substitute...")
"""

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

try:
    import nltk  # noqa: F401
    from nltk.tokenize import sent_tokenize

    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False

_STEP_MARKERS = [
    r"\bstep\s*\d+\b",
    r"\bfirst(?:ly)?\b",
    r"\bsecond(?:ly)?\b",
    r"\bthird(?:ly)?\b",
    r"\bfourth(?:ly)?\b",
    r"\bfifth(?:ly)?\b",
    r"\bthen\b",
    r"\bnext\b",
    r"\blastly\b",
    r"\bfinally\b",
    r"\bin conclusion\b",
    r"\binitially\b",
    r"\bsubsequently\b",
]
# NOTE: "then"/"next" live ONLY in _STEP_MARKERS above — they were
# previously duplicated here, double-paying every occurrence in both the
# steps and connectors tallies (and twice more via density). Likewise the
# ultra-common words "so"/"if"/"when" are excluded: nearly any English
# response contains them, so they inflated "reasoning flow" for free.
_REASONING_CONNECTORS = [
    r"\btherefore\b",
    r"\bthus\b",
    r"\bhence\b",
    r"\bbecause\b",
    r"\bhowever\b",
    r"\bmoreover\b",
    r"\bfurthermore\b",
    r"\bconsequently\b",
    r"\bimplies\b",
    r"\bmeans that\b",
    r"\bresult(?:s|ed)? in\b",
    r"\bleads? to\b",
    r"\bassuming\b",
    r"\bsuppose\b",
    r"\bin other words\b",
]
_MATH_VERBS = [
    r"\bcompute\b",
    r"\bsolve\b",
    r"\bsimplify\b",
    r"\bevaluate\b",
    r"\bsubstitute\b",
    r"\bdifferentiate\b",
    r"\bintegrate\b",
    r"\bcalculate\b",
    r"\bderivative\b",
    r"\bintegral\b",
    r"\bformula\b",
    r"\bverify\b",
    r"\bcheck\b",
]
_HEDGES = [
    r"\bmaybe\b",
    r"\bperhaps\b",
    r"\bmight\b",
    r"\buncertain\b",
    r"\bapproximately\b",
    r"\bi think\b",
    r"\broughly\b",
    r"\bnot sure\b",
    r"\bmay\b",
]


def _count_matches(text: str, patterns: list) -> int:
    total = 0
    for pat in patterns:
        total += len(re.findall(pat, text.lower()))
    return total


def _numbered_steps(text: str) -> int:
    """Detect explicitly numbered steps (1. / 1) / Step 1)."""
    return len(re.findall(r"(?:\d+[\.\)]|\bstep\s*\d+\b)", text.lower()))


def _bullet_steps(text: str) -> int:
    return len(re.findall(r"(?:^|\n)\s*(?:[-*•])\s+", text))


def score_depth(text: str) -> Dict[str, Any]:
    """Return a 0-100 reasoning-depth score for a response."""
    text = text or ""
    words = len(re.findall(r"\b[\w'-]+\b", text.lower()))
    if not text.strip():
        return {
            "depth_score": 0,
            "steps": 0,
            "connectors": 0,
            "math_verbs": 0,
            "numbered_steps": 0,
            "hedges": 0,
            "density": 0.0,
            "verdict": "empty",
        }

    steps = _count_matches(text, _STEP_MARKERS)
    connectors = _count_matches(text, _REASONING_CONNECTORS)
    math_verbs = _count_matches(text, _MATH_VERBS)
    hedges = _count_matches(text, _HEDGES)
    density = (steps + connectors) / max(words, 1) * 100

    # Bonus for math expressions (formulas, calculations)
    math_exprs = len(re.findall(r"[\d\s\+\-\*\/\=\^\(\)]{3,}", text))
    bonus = min(math_exprs * 5, 10)

    score = 0.0
    score += min(steps * 12, 40)  # explicit step structure (max 40)
    score += min(connectors * 6, 24)  # reasoning flow (max 24)
    score += min(math_verbs * 5, 15)  # math decomposition (max 15)
    score += min(density * 40, 15)  # density bonus (max 15)
    score += min(_numbered_steps(text) * 3, 6)  # explicit enumeration (max 6)
    score += bonus  # math expressions (max 10)
    score = min(100, round(score, 1))

    if score >= 60:
        verdict = "deep"
    elif score >= 30:
        verdict = "moderate"
    else:
        verdict = "shallow"

    return {
        "depth_score": score,
        "steps": steps,
        "connectors": connectors,
        "math_verbs": math_verbs,
        "hedges": hedges,
        "numbered_steps": _numbered_steps(text),
        "bullet_steps": _bullet_steps(text),
        "density": round(density, 2),
        "word_count": words,
        "verdict": verdict,
    }


def compare_depth(jarvus_text: str, baseline_text: Optional[str] = None) -> Dict[str, Any]:
    """Compare ORBIT depth against a baseline (ChatGPT)."""
    j = score_depth(jarvus_text)
    if baseline_text is None or not baseline_text.strip():
        return {"jarvus": j, "baseline": None, "jarvus_wins": j["depth_score"] > 0}

    b = score_depth(baseline_text)
    j_wins = j["depth_score"] > b["depth_score"]
    return {
        "jarvus": j,
        "baseline": b,
        "jarvus_wins": j_wins,
        "depth_delta": round(j["depth_score"] - b["depth_score"], 1),
        "verdict": "jarvus_deeper" if j_wins else ("baseline_deeper" if b["depth_score"] > j["depth_score"] else "tie"),
    }


def depth_verdict(result: Dict[str, Any]) -> str:
    return result.get("verdict", "unknown")


@dataclass
class ReasoningMetrics:
    """Sentence-level reasoning quality metrics (0-1 where applicable)."""

    depth_score: float = 0.0
    sentence_count: int = 0
    step_markers: int = 0
    reasoning_connectors: int = 0
    self_corrections: int = 0
    uncertainty: int = 0
    step_clarity: float = 0.0
    logical_consistency: float = 0.0
    verdict: str = "empty"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["depth_score"] = round(self.depth_score, 1)
        d["step_clarity"] = round(self.step_clarity, 3)
        d["logical_consistency"] = round(self.logical_consistency, 3)
        return d


_SELF_CORRECTION = [
    r"\bwait\b",
    r"\bactually\b",
    r"\bcorrection\b",
    r"\bhold on\b",
    r"\breconsider\b",
    r"\bi meant\b",
    r"\bscratch that\b",
    r"\bretract\b",
    r"\brevise\b",
    r"\bcould be wrong\b",
    r"\bhowever\b",
    r"\bon second thought\b",
    r"\bupon reflection\b",
]
_STEP_CLARITY = [
    r"\bstep\b",
    r"\bfirst\b",
    r"\bthen\b",
    r"\bnext\b",
    r"\bfinally\b",
    r"\bsecond\b",
    r"\btherefore\b",
    r"\bconclusion\b",
    r"\bin summary\b",
    r"\bfirstly\b",
    r"\blastly\b",
]
_UNCERTAINTY = [
    r"\bmaybe\b",
    r"\bperhaps\b",
    r"\bapproximately\b",
    r"\bi think\b",
    r"\broughly\b",
    r"\bnot sure\b",
    r"\buncertain\b",
    r"\bcould\b",
    r"\bmight\b",
    r"\bpossibly\b",
    r"\babout\b",
    r"\bapprox\b",
    r"\bestimate\b",
    r"\bguess\b",
]


def _split_sentences(text: str) -> List[str]:
    """NLTK-aware sentence splitting with regex fallback (zero deps)."""
    if not text or not text.strip():
        return []
    if NLTK_AVAILABLE:
        try:
            return [s for s in sent_tokenize(text) if s.strip()]
        except Exception:
            pass
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


class ReasoningDepthAnalyzer:
    """Sentence-level reasoning analysis. NLTK optional; never raises on import."""

    def _count(self, text: str, patterns: list) -> int:
        return sum(len(re.findall(p, text.lower())) for p in patterns)

    def analyze(self, text: str) -> ReasoningMetrics:
        text = text or ""
        if not text.strip():
            return ReasoningMetrics(verdict="empty")

        sentences = _split_sentences(text)
        n = len(sentences)
        steps = self._count(text, _STEP_MARKERS)
        connectors = self._count(text, _REASONING_CONNECTORS)
        corrections = self._count(text, _SELF_CORRECTION)
        uncertainty = self._count(text, _UNCERTAINTY)

        # Structural proxies for clarity / consistency (0-1)
        step_clarity = min(1.0, (steps + connectors) / max(n, 1) / 2.0)
        logical_consistency = min(1.0, connectors / max(n, 1))
        if n >= 3:
            logical_consistency = min(1.0, logical_consistency + 0.25)  # multi-sentence = more coherent
        elif n == 1:
            logical_consistency = max(0.0, logical_consistency - 0.2)

        # Reuse the fast heuristic scorer for the headline number
        base = score_depth(text)
        depth = base["depth_score"]
        if corrections and depth > 0:
            depth = min(100.0, depth + min(corrections * 3, 9))  # honest correction bonus
        depth = min(100.0, round(depth, 1))

        verdict = "deep" if depth >= 60 else ("moderate" if depth >= 30 else "shallow")
        return ReasoningMetrics(
            depth_score=depth,
            sentence_count=n,
            step_markers=steps,
            reasoning_connectors=connectors,
            self_corrections=corrections,
            uncertainty=uncertainty,
            step_clarity=step_clarity,
            logical_consistency=logical_consistency,
            verdict=verdict,
        )

    def analyze_dict(self, text: str) -> Dict[str, Any]:
        return self.analyze(text).to_dict()

    def compare(self, jarvus_text: str, baseline_text: Optional[str] = None) -> Dict[str, Any]:
        j = self.analyze(jarvus_text)
        if baseline_text is None or not baseline_text.strip():
            return {"jarvus": j.to_dict(), "baseline": None, "jarvus_wins": j.depth_score > 0}
        b = self.analyze(baseline_text)
        j_wins = j.depth_score > b.depth_score
        return {
            "jarvus": j.to_dict(),
            "baseline": b.to_dict(),
            "jarvus_wins": j_wins,
            "depth_delta": round(j.depth_score - b.depth_score, 1),
            "verdict": "jarvus_deeper" if j_wins else ("baseline_deeper" if b.depth_score > j.depth_score else "tie"),
        }


_analyzer: Optional[ReasoningDepthAnalyzer] = None


def get_analyzer() -> ReasoningDepthAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = ReasoningDepthAnalyzer()
    return _analyzer


def analyze_reasoning(text: str) -> Dict[str, Any]:
    """Module-level convenience: sentence-level analysis dict."""
    return get_analyzer().analyze_dict(text)


def compare_analyzer(jarvus_text: str, baseline_text: Optional[str] = None) -> Dict[str, Any]:
    """Module-level convenience: analyzer-backed compare."""
    return get_analyzer().compare(jarvus_text, baseline_text)
