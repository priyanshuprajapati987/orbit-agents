# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
Smart Memory — remembers user preferences, patterns, and agent history.

Makes ORBIT learn:
  - User prefers which agents for which tasks
  - Time-of-day patterns (morning = news, evening = code)
  - Frequently asked topics
  - Correction history (what user corrected)
  - Preferred response style (short vs detailed)
"""

import json
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from .runtime_paths import atomic_write_text

logger = logging.getLogger(__name__)


_TOPIC_STOP_WORDS = frozenset({
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "shall",
            "can",
            "need",
            "dare",
            "ought",
            "used",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "out",
            "off",
            "over",
            "under",
            "again",
            "further",
            "then",
            "once",
            "here",
            "there",
            "when",
            "where",
            "why",
            "how",
            "all",
            "each",
            "every",
            "both",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "nor",
            "not",
            "only",
            "own",
            "same",
            "so",
            "than",
            "too",
            "very",
            "just",
            "don",
            "now",
            "tell",
            "me",
            "about",
            "what",
            "which",
            "who",
            "whom",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
            "i",
            "my",
            "me",
            "we",
            "our",
            "you",
            "your",
            "he",
            "him",
            "his",
            "she",
            "her",
            "they",
            "them",
            "their",
            "and",
            "but",
            "or",
            "if",
            "because",
            "while",
            "although",
            "until",
            "since",
            "unless",
            "get",
            "make",
            "let",
            "give",
            "also",
            "like",
            "just",
            "want",
            "know",
            "see",
            "use",
            "try",
            "go",
            # Hindi stop words
            "mai",
            "mera",
            "meri",
            "mere",
            "tum",
            "tumhara",
            "aap",
            "yeh",
            "woh",
            "kya",
            "kaise",
            "kab",
            "kahan",
            "kaun",
            "kitna",
            "phir",
            "aur",
            "bhi",
            "hai",
            "ho",
            "hain",
            "tha",
            "thi",
            "the",
            "hoga",
            "hogi",
            "honge",
            "kar",
            "karo",
            "karna",
            "kare",
            "karta",
            "karti",
            "karenge",
            "se",
            "ko",
            "ke",
            "ka",
            "ki",
            "mein",
            "par",
            "pe",
            "ne",
            "nahi",
            "mat",
            "wo",
            "ye",
            "us",
            "is",
            "usko",
            "isko",
            "kuch",
            "sab",
            "har",
            "koi",
            "kisi",
            "jaise",
            "waise",
})

def _get_data_dir():
    from .runtime_paths import orbit_data_dir
    return orbit_data_dir() / "smart_memory"


@dataclass
class UserPreference:
    key: str
    value: str
    confidence: float = 0.5
    last_updated: float = 0
    evidence: int = 0


@dataclass
class AgentPattern:
    agent: str
    task_pattern: str
    success_count: int = 0
    fail_count: int = 0
    avg_latency_ms: float = 0
    preferred_time: str = ""  # "morning", "afternoon", "evening", "night"


@dataclass
class Correction:
    original_query: str
    wrong_agent: str
    correct_agent: str
    timestamp: float = 0


class SmartMemory:
    """Learns user patterns and preferences over time."""

    def __init__(self):
        self._preferences: Dict[str, UserPreference] = {}
        self._agent_patterns: Dict[str, AgentPattern] = {}
        self._corrections: List[Correction] = []
        self._topic_frequency: Dict[str, int] = defaultdict(int)
        self._time_patterns: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._recent_queries: deque = deque(maxlen=50)
        self._lock = threading.RLock()
        self._query_lengths: list = []
        self._expertise_level: str = "intermediate"
        self._load()

    # ── Preferences ──────────────────────────────────────────────────────

    def set_preference(self, key: str, value: str, confidence: float = 0.7):
        with self._lock:
            if key in self._preferences:
                pref = self._preferences[key]
                if pref.value == value:
                    pref.confidence = min(pref.confidence + 0.1, 1.0)
                    pref.evidence += 1
                else:
                    # New value — lower confidence until proven
                    pref.value = value
                    pref.confidence = max(confidence - 0.2, 0.3)
                    pref.evidence = 1
                pref.last_updated = time.time()
            else:
                self._preferences[key] = UserPreference(
                    key=key,
                    value=value,
                    confidence=confidence,
                    last_updated=time.time(),
                    evidence=1,
                )
        self._save()

    def get_preference(self, key: str) -> Optional[str]:
        with self._lock:
            pref = self._preferences.get(key)
            if pref and pref.confidence > 0.4:
                return pref.value
        return None

    def learn_preference(self, query: str, agent: str, success: bool):
        """Auto-learn preferences from interactions."""
        # Track query length for expertise inference
        with self._lock:
            self._query_lengths.append(len(query.split()))
            if len(self._query_lengths) > 100:
                self._query_lengths = self._query_lengths[-50:]
            self.infer_expertise()

        # Agent preference per topic
        topic = self._extract_topic(query)
        if topic:
            key = f"agent_for_{topic}"
            if success:
                self.set_preference(key, agent, confidence=0.6)
            else:
                # Learn from failure: lower confidence for this agent on this topic
                with self._lock:
                    pref = self._preferences.get(key)
                    if pref and pref.value == agent:
                        pref.confidence = max(pref.confidence - 0.15, 0.1)
                        pref.evidence += 1
                        pref.last_updated = time.time()
                        logger.debug(
                            "Decreased confidence for agent=%s on topic=%s to %.2f", agent, topic, pref.confidence
                        )

        # Response style preference
        if len(query.split()) <= 3:
            self.set_preference("response_style", "concise", confidence=0.5)
        elif len(query.split()) > 20:
            self.set_preference("response_style", "detailed", confidence=0.5)

    # ── Agent Patterns ───────────────────────────────────────────────────

    def record_agent_usage(self, agent: str, query: str, success: bool, latency_ms: float = 0):
        topic = self._extract_topic(query)[:50]
        time_slot = self._get_time_slot()

        # Track query length for expertise inference
        with self._lock:
            self._query_lengths.append(len(query.split()))
            if len(self._query_lengths) > 100:
                self._query_lengths = self._query_lengths[-50:]
            self.infer_expertise()

        with self._lock:
            key = f"{agent}:{topic}"
            if key not in self._agent_patterns:
                self._agent_patterns[key] = AgentPattern(
                    agent=agent,
                    task_pattern=topic,
                    preferred_time=time_slot,
                )
            p = self._agent_patterns[key]
            if success:
                p.success_count += 1
            else:
                p.fail_count += 1
            # Running average latency
            total = p.success_count + p.fail_count
            p.avg_latency_ms = ((p.avg_latency_ms * (total - 1)) + latency_ms) / total

            # Track time patterns
            self._time_patterns[time_slot][agent] += 1

        self._save()

    def get_best_agent_for_topic(self, topic: str) -> Optional[str]:
        # Truncate like record_agent_usage does ([:50]): otherwise a long
        # query topic never substring-matches its own stored pattern.
        topic = (topic or "")[:50]
        with self._lock:
            best = None
            best_score = -1
            for key, pattern in self._agent_patterns.items():
                if topic.lower() in pattern.task_pattern.lower():
                    total = pattern.success_count + pattern.fail_count
                    if total < 3:
                        continue
                    score = pattern.success_count / max(total, 1)
                    if score > best_score:
                        best_score = score
                        best = pattern.agent
            return best

    def get_worst_agent_for_topic(self, topic: str) -> Optional[str]:
        """Return the agent with lowest success rate for this topic (to avoid)."""
        topic = (topic or "")[:50]
        with self._lock:
            worst = None
            worst_score = 2.0
            for key, pattern in self._agent_patterns.items():
                if topic.lower() in pattern.task_pattern.lower():
                    total = pattern.success_count + pattern.fail_count
                    if total < 3:
                        continue
                    score = pattern.success_count / max(total, 1)
                    if score < worst_score:
                        worst_score = score
                        worst = pattern.agent
            # Only return if success rate is genuinely bad (< 40%)
            if worst and worst_score < 0.4:
                return worst
            return None

    def get_agent_success_rate(self, agent: str, topic: str = "") -> float:
        """Return success rate (0.0-1.0) for an agent, optionally filtered by topic."""
        topic = (topic or "")[:50]
        with self._lock:
            rates = []
            for key, pattern in self._agent_patterns.items():
                if pattern.agent != agent:
                    continue
                if topic and topic.lower() not in pattern.task_pattern.lower():
                    continue
                total = pattern.success_count + pattern.fail_count
                if total > 0:
                    rates.append(pattern.success_count / total)
            return sum(rates) / len(rates) if rates else 0.5

    def get_agent_performance_stats(self) -> Dict[str, Dict]:
        """Return performance stats for all agents (for dashboard)."""
        with self._lock:
            agent_stats: Dict[str, Dict] = {}
            for key, pattern in self._agent_patterns.items():
                agent = pattern.agent
                if agent not in agent_stats:
                    agent_stats[agent] = {
                        "agent": agent,
                        "total_calls": 0,
                        "successes": 0,
                        "failures": 0,
                        "success_rate": 0.0,
                        "avg_latency_ms": 0.0,
                        "topics": [],
                        "best_topic": "",
                        "worst_topic": "",
                    }
                stats = agent_stats[agent]
                total = pattern.success_count + pattern.fail_count
                stats["total_calls"] += total
                stats["successes"] += pattern.success_count
                stats["failures"] += pattern.fail_count
                if pattern.task_pattern not in stats["topics"]:
                    stats["topics"].append(pattern.task_pattern)

            # Calculate rates and find best/worst topics
            for agent, stats in agent_stats.items():
                if stats["total_calls"] > 0:
                    stats["success_rate"] = stats["successes"] / stats["total_calls"]
                # Find best/worst topic
                topic_rates = []
                for key, pattern in self._agent_patterns.items():
                    if pattern.agent == agent:
                        total = pattern.success_count + pattern.fail_count
                        if total >= 3:
                            rate = pattern.success_count / total
                            topic_rates.append((pattern.task_pattern, rate))
                if topic_rates:
                    topic_rates.sort(key=lambda x: x[1], reverse=True)
                    stats["best_topic"] = topic_rates[0][0]
                    stats["worst_topic"] = topic_rates[-1][0] if topic_rates[-1][1] < 0.5 else ""

            return agent_stats

    def get_preferred_topic(self) -> Optional[str]:
        """Return the most frequently asked topic (for proactive suggestions)."""
        with self._lock:
            if not self._topic_frequency:
                return None
            # Return topic with highest frequency that's not too generic
            sorted_topics = sorted(
                self._topic_frequency.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            for topic, count in sorted_topics:
                if count >= 3 and topic not in ("general", "chat", "hello", "hi"):
                    return topic
            return None

    def get_time_based_agent(self) -> Optional[str]:
        """Get most-used agent for current time of day."""
        slot = self._get_time_slot()
        with self._lock:
            agents = self._time_patterns.get(slot, {})
            if agents:
                return max(agents, key=agents.get)
        return None

    # ── Corrections ──────────────────────────────────────────────────────

    def record_correction(self, query: str, wrong_agent: str, correct_agent: str):
        with self._lock:
            self._corrections.append(
                Correction(
                    original_query=query,
                    wrong_agent=wrong_agent,
                    correct_agent=correct_agent,
                    timestamp=time.time(),
                )
            )
            # Keep last 100 corrections
            if len(self._corrections) > 100:
                self._corrections = self._corrections[-100:]
        self._save()

    def get_correction_pattern(self, query: str) -> Optional[str]:
        """Check if similar query was corrected before."""
        topic = self._extract_topic(query)
        with self._lock:
            for c in reversed(self._corrections):
                if topic and topic in c.original_query.lower():
                    return c.correct_agent
        return None

    def build_personalization_context(self, query: str = "") -> Optional[str]:
        """Compact user-memory block for injection into the LLM prompt.

        Surfaces learned preferences (top by confidence), past corrections,
        agent success rates, and topic patterns so ORBIT tailors answers.
        Returns None when there is nothing to personalize with.
        """
        lines: List[str] = []
        with self._lock:
            # Top preferences
            prefs = sorted(
                self._preferences.items(),
                key=lambda kv: kv[1].confidence,
                reverse=True,
            )
            count = 0
            for k, p in prefs:
                if count >= 8:
                    break
                if p.confidence < 0.5:
                    continue
                if k.startswith("agent_for_query_"):
                    continue
                lines.append(f"- User preference ({k}): {p.value}")
                count += 1

            # Past corrections
            corr = self.get_correction_pattern(query)
            if corr:
                lines.append(f"- For similar past queries the user corrected to: {corr}")

            # Frequent topics
            frequent = self.get_frequent_topics(3)
            if frequent:
                topics_str = ", ".join(t[0] for t in frequent)
                lines.append(f"- Recent topics: {topics_str}")

            # Agent success rates for this query's topic
            topic = self._extract_topic(query)
            if topic:
                best = self.get_best_agent_for_topic(topic)
                worst = self.get_worst_agent_for_topic(topic)
                if best:
                    lines.append(f"- Best agent for '{topic}': {best}")
                if worst:
                    lines.append(f"- Avoid agent for '{topic}': {worst} (low success rate)")

            # Time-based patterns
            time_agent = self.get_time_based_agent()
            if time_agent:
                lines.append(f"- Usually used at this time: {time_agent}")

        if not lines:
            return None
        return "[User memory]\n" + "\n".join(lines)

    # ── Topic Tracking ───────────────────────────────────────────────────

    def record_topic(self, query: str):
        topic = self._extract_topic(query)
        if topic:
            with self._lock:
                self._topic_frequency[topic] += 1
                self._recent_queries.append((query, time.time()))
        self._save()

    def get_frequent_topics(self, limit: int = 10) -> List[Tuple[str, int]]:
        with self._lock:
            sorted_topics = sorted(self._topic_frequency.items(), key=lambda x: x[1], reverse=True)
        return sorted_topics[:limit]

    # ── Helpers ──────────────────────────────────────────────────────────

    def _extract_topic(self, query: str) -> str:
        """Extract main topic from query."""
        words = query.lower().split()
        stop = _TOPIC_STOP_WORDS
        meaningful = [w for w in words if w not in stop and len(w) > 2]
        return " ".join(meaningful[:3]) if meaningful else query[:30]

    def infer_expertise(self) -> str:
        """Infer user expertise from query patterns."""
        if len(self._query_lengths) < 5:
            return "intermediate"
        avg_len = sum(self._query_lengths) / len(self._query_lengths)
        if avg_len > 20:
            self._expertise_level = "expert"
        elif avg_len < 5:
            self._expertise_level = "beginner"
        else:
            self._expertise_level = "intermediate"
        return self._expertise_level

    def _get_time_slot(self) -> str:
        import datetime

        hour = datetime.datetime.now().hour
        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 21:
            return "evening"
        else:
            return "night"

    # ── Confidence Decay ────────────────────────────────────────────────

    def decay_confidence(self, decay_rate: float = 0.02):
        """Decay confidence of all preferences over time. Old, unused preferences
        naturally fade away. Called periodically (e.g. every 100 interactions)."""
        now = time.time()
        with self._lock:
            for pref in self._preferences.values():
                if pref.last_updated == 0:
                    continue
                age_hours = (now - pref.last_updated) / 3600
                if age_hours > 24:
                    # Decay proportional to age: 2% per day, max 50% total
                    days_old = age_hours / 24
                    decay = min(decay_rate * days_old, 0.5)
                    pref.confidence = max(pref.confidence - decay, 0.05)
            # Remove dead preferences (confidence < 0.1 and old)
            dead = [
                k for k, v in self._preferences.items() if v.confidence < 0.1 and (now - v.last_updated) / 3600 > 168
            ]  # 7 days
            for k in dead:
                del self._preferences[k]
                logger.debug("Pruned dead preference: %s", k)

    # ── Persistence ──────────────────────────────────────────────────────

    def _save(self):
        # Snapshot under lock: _save is called lock-free from several
        # paths while writers mutate these structures under RLock.
        # Dumping live dicts tears ("dictionary changed size") and the
        # save is then silently skipped.
        with self._lock:
            data = {
                "preferences": {
                    k: {
                        "value": v.value,
                        "confidence": v.confidence,
                        "evidence": v.evidence,
                        "last_updated": v.last_updated,
                    }
                    for k, v in self._preferences.items()
                },
                "corrections": [
                    {"query": c.original_query, "wrong": c.wrong_agent, "correct": c.correct_agent, "ts": c.timestamp}
                    for c in self._corrections[-50:]
                ],
                "topics": dict(self._topic_frequency),
                "time_patterns": {k: dict(v) for k, v in self._time_patterns.items()},
                "query_lengths": list(self._query_lengths[-100:]),
                "agent_patterns": {
                    k: {
                        "success": p.success_count,
                        "fail": p.fail_count,
                        "latency": p.avg_latency_ms,
                    }
                    for k, p in self._agent_patterns.items()
                },
                "expertise_level": self._expertise_level,
            }
        try:
            _get_data_dir().mkdir(parents=True, exist_ok=True)
            atomic_write_text((_get_data_dir() / "memory.json"), json.dumps(data, indent=2))
        except Exception:
            pass

    def _load(self):
        try:
            path = _get_data_dir() / "memory.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                for k, v in data.get("preferences", {}).items():
                    self._preferences[k] = UserPreference(
                        key=k,
                        value=v["value"],
                        confidence=v.get("confidence", 0.5),
                        evidence=v.get("evidence", 1),
                        last_updated=v.get("last_updated", 0),
                    )
                for c in data.get("corrections", []):
                    self._corrections.append(
                        Correction(
                            original_query=c["query"],
                            wrong_agent=c["wrong"],
                            correct_agent=c["correct"],
                            timestamp=c.get("ts", 0),
                        )
                    )
                for topic, count in data.get("topics", {}).items():
                    self._topic_frequency[topic] = count
                for slot, agents in data.get("time_patterns", {}).items():
                    for agent, count in agents.items():
                        self._time_patterns[slot][agent] = count
                self._query_lengths = data.get("query_lengths", [])
                self._expertise_level = data.get("expertise_level", "intermediate")
                # Decay old preferences on load
                self.decay_confidence()
        except Exception:
            pass

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "preferences": len(self._preferences),
                "corrections": len(self._corrections),
                "topics_tracked": len(self._topic_frequency),
                "top_topics": self.get_frequent_topics(5),
                "agent_patterns": len(self._agent_patterns),
                "expertise_level": self._expertise_level,
                "query_samples": len(self._query_lengths),
            }


# Singleton
_memory = None
_lock = threading.Lock()


def get_smart_memory() -> SmartMemory:
    global _memory
    if _memory is None:
        with _lock:
            if _memory is None:
                _memory = SmartMemory()
    return _memory
