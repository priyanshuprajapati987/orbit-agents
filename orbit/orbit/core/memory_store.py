# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Shared Memory Store — Cross-Agent Memory System

Patterns from NirDiamant/GenAI_Agents:
- Blackboard architecture for cross-agent sharing
- Multi-factor relevance scoring (recency + frequency + source)
- Exponential decay for stale memory cleanup (Ebbinghaus forgetting curve)

Usage:
    from orbit.orbit.core.memory_store import SharedMemoryPool

    pool = SharedMemoryPool()
    pool.write("COD", "code", "auth_fix", {"solution": "Use bcrypt", "confidence": 0.9})
    results = pool.search("authentication", agent_id="RES")
    pool.cleanup_stale()
"""

from .runtime_paths import data_dir
import copy
import json
import math
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)


# ── Permission model ────────────────────────────────────────────────────────
class Permission(Enum):
    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"
    NONE = "none"


# ── Memory entry ────────────────────────────────────────────────────────────
@dataclass
class MemoryEntry:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    namespace: str = "global"
    key: str = ""
    value: Any = None
    source_agent: str = "unknown"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_accessed: str = field(default_factory=lambda: datetime.now().isoformat())
    access_count: int = 0
    strength: float = 1.0
    tags: List[str] = field(default_factory=list)
    version: int = 1
    # Owner. "local" = pre-tenancy solo data. The store key includes it,
    # so two users never collide on namespace/key, and every read path
    # filters on it (row-level isolation).
    user_id: str = "local"


# ── Relevance scorer (multi-factor) ────────────────────────────────────────
class RelevanceScorer:
    """Scores memory relevance using recency + frequency + source reliability.

    Formula:
        score = 0.4 * recency + 0.3 * frequency + 0.3 * source_weight

    Recency: exponential decay — exp(-decay_rate * age_hours)
    Frequency: access_count / max_access_count (normalized)
    Source: user=1.0, agent=0.7, tool=0.5, unknown=0.3
    """

    DECAY_RATE = math.log(2) / 168.0  # half-life = 7 days (168 hours)
    SOURCE_WEIGHTS = {
        "user": 1.0,
        "QUEEN": 0.85,
        "CNC": 0.80,
        "MEM": 0.75,
        "COD": 0.7,
        "RES": 0.7,
        "WRI": 0.7,
        "DEC": 0.7,
        "SEC": 0.7,
        "PLN": 0.7,
        "EXE": 0.7,
        "CRT": 0.7,
        "TAS": 0.65,
        "SCR": 0.65,
        "SOC": 0.65,
        "MTG": 0.65,
        "FTN": 0.65,
        "THB": 0.65,
        "SEO": 0.65,
        "QNT": 0.65,
        "LOG": 0.65,
        "tool": 0.5,
        "unknown": 0.3,
    }

    def __init__(self):
        self._max_access = 1

    def update_max_access(self, entries: List[MemoryEntry]):
        if entries:
            self._max_access = max(e.access_count for e in entries) or 1

    def score(self, entry: MemoryEntry, query: str = "") -> float:
        recency = self._recency_score(entry)
        frequency = self._frequency_score(entry)
        source = self._source_score(entry)
        text_boost = self._text_relevance(entry, query) if query else 0.0

        base = 0.4 * recency + 0.3 * frequency + 0.3 * source
        return min(1.0, base + text_boost)

    def _recency_score(self, entry: MemoryEntry) -> float:
        try:
            last = datetime.fromisoformat(entry.last_accessed)
            age_hours = (datetime.now() - last).total_seconds() / 3600.0
            return math.exp(-self.DECAY_RATE * age_hours)
        except (ValueError, TypeError):
            return 0.5

    def _frequency_score(self, entry: MemoryEntry) -> float:
        return min(1.0, entry.access_count / self._max_access) if self._max_access > 0 else 0.0

    def _source_score(self, entry: MemoryEntry) -> float:
        return self.SOURCE_WEIGHTS.get(entry.source_agent, 0.3)

    def _text_relevance(self, entry: MemoryEntry, query: str) -> float:
        query_lower = query.lower()
        entry_text = json.dumps(entry.value, default=str).lower() if entry.value else ""
        entry_text += " " + entry.key.lower() + " " + " ".join(entry.tags).lower()

        words = query_lower.split()
        matches = sum(1 for w in words if w in entry_text)
        return min(0.3, (matches / len(words)) * 0.3) if words else 0.0


# ── Decay engine (Ebbinghaus forgetting curve) ─────────────────────────────
class DecayEngine:
    """Applies exponential decay to memory strength.

    Retrieved memories get reinforced, so frequently-used memories stay strong
    while unused ones fade. Inspired by spaced repetition.
    """

    PRUNE_THRESHOLD = 0.1

    def __init__(self, half_life_hours: float = 168.0):
        self.decay_rate = math.log(2) / half_life_hours

    def apply_decay(self, entry: MemoryEntry) -> MemoryEntry:
        try:
            last = datetime.fromisoformat(entry.last_accessed)
            hours_idle = (datetime.now() - last).total_seconds() / 3600.0
            entry.strength *= math.exp(-self.decay_rate * hours_idle)
            entry.strength = max(0.0, entry.strength)
        except (ValueError, TypeError):
            pass
        return entry

    def reinforce(self, entry: MemoryEntry, boost: float = 0.3):
        entry.strength = min(1.0, entry.strength + boost)
        entry.access_count += 1
        entry.last_accessed = datetime.now().isoformat()

    def should_prune(self, entry: MemoryEntry) -> bool:
        return entry.strength < self.PRUNE_THRESHOLD


# ── Access control ──────────────────────────────────────────────────────────
# NOTE (accepted risk, single-user desktop): agent identity comes from the
# request body and the default permission is READ_WRITE, so memory
# namespaces are NOT isolated against a key holder spoofing agent_id.
# Real isolation needs the agent_id bound to an authenticated principal
# (no user model exists yet). Do not rely on namespaces for secrecy.
class AccessControl:
    """Per-agent, per-namespace permissions."""

    def __init__(self):
        self._rules: Dict[str, Dict[str, Permission]] = {}
        self._default = Permission.READ_WRITE

    def grant(self, agent_id: str, namespace: str, permission: Permission):
        if agent_id not in self._rules:
            self._rules[agent_id] = {}
        self._rules[agent_id][namespace] = permission

    def revoke(self, agent_id: str, namespace: str):
        if agent_id in self._rules:
            self._rules[agent_id].pop(namespace, None)

    def can_read(self, agent_id: str, namespace: str) -> bool:
        perm = self._rules.get(agent_id, {}).get(namespace, self._default)
        return perm in (Permission.READ, Permission.READ_WRITE)

    def can_write(self, agent_id: str, namespace: str) -> bool:
        perm = self._rules.get(agent_id, {}).get(namespace, self._default)
        return perm in (Permission.WRITE, Permission.READ_WRITE)


# ── Shared memory pool (Blackboard) ────────────────────────────────────────
class SharedMemoryPool:
    """Thread-safe shared memory with namespace partitioning, relevance scoring,
    and exponential decay cleanup.

    All 21 agents can read/write through a central store. Memory entries are
    scored by relevance (recency + frequency + source) and pruned when stale.
    """

    def __init__(self, storage_file: Optional[str] = None):
        self._lock = threading.Lock()
        self._store: Dict[str, MemoryEntry] = {}
        self.access_control = AccessControl()
        self.scorer = RelevanceScorer()
        self.decay = DecayEngine()

        self.storage_file = storage_file or str(data_dir() / "shared_memory.json")
        self._load()

    # ── persistence ─────────────────────────────────────────────────────────
    def _load(self):
        try:
            p = Path(self.storage_file)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                for raw in data:
                    try:
                        if not isinstance(raw, dict):
                            continue
                        entry = MemoryEntry(**raw)
                    except (TypeError, KeyError, ValueError):
                        continue
                    # Backfill pre-tenancy rows (no user_id) as "local" and
                    # re-key old `ns/key` entries to `user/ns/key`.
                    if not getattr(entry, "user_id", ""):
                        entry.user_id = "local"
                    self._store[f"{entry.user_id}/{entry.namespace}/{entry.key}"] = entry
        except Exception as e:
            logger.error("Failed to load shared memory: %s", e)

    def _save(self):
        try:
            p = Path(self.storage_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            data = [asdict(e) for e in self._store.values()]
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(p)
        except Exception as e:
            logger.error("Failed to save shared memory: %s", e)

    # ── CRUD ────────────────────────────────────────────────────────────────
    def _nk(self, user_id: str, namespace: str, key: str) -> str:
        return f"{user_id or 'local'}/{namespace}/{key}"

    def write(
        self, agent_id: str, namespace: str, key: str, value: Any, tags: Optional[List[str]] = None,
        user_id: str = "local",
    ) -> Optional[MemoryEntry]:
        user_id = user_id or "local"
        with self._lock:
            if not self.access_control.can_write(agent_id, namespace):
                logger.warning("Write denied: %s -> %s", agent_id, namespace)
                return None

            nk = self._nk(user_id, namespace, key)
            existing = self._store.get(nk)
            if existing:
                existing.value = value
                existing.tags = tags or existing.tags
                existing.last_accessed = datetime.now().isoformat()
                existing.version += 1
                self.decay.reinforce(existing, boost=0.1)
                entry = existing
            else:
                entry = MemoryEntry(
                    namespace=namespace,
                    key=key,
                    value=value,
                    source_agent=agent_id,
                    tags=tags or [],
                    user_id=user_id,
                )
                self._store[nk] = entry

            self._save()
            return entry

    def read(self, agent_id: str, namespace: str, key: str, user_id: str = "local") -> Optional[MemoryEntry]:
        user_id = user_id or "local"
        with self._lock:
            if not self.access_control.can_read(agent_id, namespace):
                return None
            entry = self._store.get(self._nk(user_id, namespace, key))
            if entry:
                self.decay.reinforce(entry, boost=0.05)
                self._save()
                return copy.deepcopy(entry)
            return None

    def read_all_accessible(
        self, agent_id: str, namespaces: Optional[List[str]] = None, user_id: str = "local"
    ) -> List[MemoryEntry]:
        user_id = user_id or "local"
        with self._lock:
            results = []
            for nk, entry in self._store.items():
                if getattr(entry, "user_id", "local") != user_id:
                    continue
                ns = entry.namespace
                if namespaces and ns not in namespaces:
                    continue
                if self.access_control.can_read(agent_id, ns):
                    results.append(entry)
            return results

    def delete(self, agent_id: str, namespace: str, key: str, user_id: str = "local") -> bool:
        user_id = user_id or "local"
        with self._lock:
            if not self.access_control.can_write(agent_id, namespace):
                return False
            nk = self._nk(user_id, namespace, key)
            if nk in self._store:
                del self._store[nk]
                self._save()
                return True
            return False

    def delete_by_id(self, agent_id: str, entry_id: str, user_id: str = "local") -> bool:
        """Delete a memory by its entry id (resolves namespace/key)."""
        user_id = user_id or "local"
        with self._lock:
            for nk, entry in list(self._store.items()):
                if getattr(entry, "id", None) == entry_id:
                    if getattr(entry, "user_id", "local") != user_id:
                        return False
                    if not self.access_control.can_write(agent_id, entry.namespace):
                        return False
                    del self._store[nk]
                    self._save()
                    return True
            return False

    # ── search with relevance scoring ───────────────────────────────────────
    def search(
        self,
        query: str,
        agent_id: str = "unknown",
        namespaces: Optional[List[str]] = None,
        limit: int = 10,
        compact: bool = False,
        user_id: str = "local",
    ) -> List[Dict]:
        """Search memories with relevance scoring.

        Args:
            compact: If True, return only IDs and summaries (fast overview).
                     If False, return full value objects (default).

        Concurrency: the snapshot + decay happen under lock, but scoring
        (pure CPU over all entries) and deepcopy run OUTSIDE it — holding
        one lock across the full scan serialized all searches (measured
        p50 scaling linearly with worker count). Reinforcement of the top
        hits re-acquires the lock briefly.
        """
        user_id = user_id or "local"
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 10
        with self._lock:
            entries = list(self._store.values())
            for entry in entries:
                self.decay.apply_decay(entry)
        scored = []
        for entry in entries:
            if getattr(entry, "user_id", "local") != user_id:
                continue
            ns = entry.namespace
            if namespaces and ns not in namespaces:
                continue
            if not self.access_control.can_read(agent_id, ns):
                continue
            scored.append((self.scorer.score(entry, query), entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        top = scored[:limit]
        with self._lock:
            for score, entry in top:
                self.decay.reinforce(entry, boost=0.05)
                if compact:
                    summary = ""
                    if isinstance(entry.value, dict):
                        summary = entry.value.get("summary", str(entry.value)[:80])
                    else:
                        summary = str(entry.value)[:80]
                    results.append(
                        {
                            "id": entry.id,
                            "key": entry.key,
                            "namespace": entry.namespace,
                            "summary": summary,
                            "source_agent": entry.source_agent,
                            "relevance_score": round(score, 4),
                            "created_at": entry.created_at,
                        }
                    )
                else:
                    results.append(
                        {
                            "key": entry.key,
                            "namespace": entry.namespace,
                            "value": copy.deepcopy(entry.value),
                            "source_agent": entry.source_agent,
                            "relevance_score": round(score, 4),
                            "strength": round(entry.strength, 4),
                            "access_count": entry.access_count,
                            "created_at": entry.created_at,
                            "tags": entry.tags,
                        }
                    )
            self._maybe_save_locked()
        return results

    def _maybe_save_locked(self) -> None:
        """Persist at most every N searches. Assumes self._lock is held.

        search() used to fsync the whole store on EVERY call (full JSON
        dump under lock) — the dominant cost at concurrency.
        """
        self._searches_since_save = getattr(self, "_searches_since_save", 0) + 1
        if self._searches_since_save >= 20:
            self._searches_since_save = 0
            self._save()

    # ── get full observation by ID ──────────────────────────────────────────
    def get_observation(self, entry_id: str, user_id: str = "local") -> Optional[Dict]:
        """Get full memory details by compact ID (from search(compact=True))."""
        user_id = user_id or "local"
        with self._lock:
            for entry in self._store.values():
                if entry.id == entry_id and getattr(entry, "user_id", "local") == user_id:
                    self.decay.reinforce(entry, boost=0.1)
                    self._save()
                    return {
                        "id": entry.id,
                        "key": entry.key,
                        "namespace": entry.namespace,
                        "value": copy.deepcopy(entry.value),
                        "source_agent": entry.source_agent,
                        "created_at": entry.created_at,
                        "last_accessed": entry.last_accessed,
                        "access_count": entry.access_count,
                        "strength": round(entry.strength, 4),
                        "tags": entry.tags,
                        "version": entry.version,
                    }
            return None

    # ── timeline view (chronological) ───────────────────────────────────────
    def timeline(
        self,
        agent_id: str = "unknown",
        namespaces: Optional[List[str]] = None,
        limit: int = 20,
        descending: bool = True,
        user_id: str = "local",
    ) -> List[Dict]:
        """Get memories in chronological order (newest or oldest first)."""
        user_id = user_id or "local"
        with self._lock:
            entries = []
            for entry in self._store.values():
                if getattr(entry, "user_id", "local") != user_id:
                    continue
                ns = entry.namespace
                if namespaces and ns not in namespaces:
                    continue
                if self.access_control.can_read(agent_id, ns):
                    entries.append(entry)

            entries.sort(key=lambda e: e.created_at, reverse=descending)

            results = []
            for entry in entries[:limit]:
                summary = ""
                if isinstance(entry.value, dict):
                    summary = entry.value.get("summary", str(entry.value)[:100])
                else:
                    summary = str(entry.value)[:100]
                results.append(
                    {
                        "id": entry.id,
                        "key": entry.key,
                        "namespace": entry.namespace,
                        "summary": summary,
                        "source_agent": entry.source_agent,
                        "created_at": entry.created_at,
                        "strength": round(entry.strength, 4),
                    }
                )
            return results

    # ── stale memory cleanup ────────────────────────────────────────────────
    def cleanup_stale(self) -> Dict:
        with self._lock:
            self.scorer.update_max_access(list(self._store.values()))

            pruned = []
            for nk, entry in list(self._store.items()):
                self.decay.apply_decay(entry)
                if self.decay.should_prune(entry):
                    pruned.append(
                        {"key": entry.key, "namespace": entry.namespace, "strength": round(entry.strength, 4)}
                    )
                    del self._store[nk]

            deduped = self._deduplicate()
            pruned.extend(deduped)

            self._save()
            logger.info("Memory cleanup: %d pruned, %d deduped", len(pruned) - len(deduped), len(deduped))
            return {"pruned": len(pruned), "remaining": len(self._store), "details": pruned}

    def _deduplicate(self) -> List[Dict]:
        from collections import defaultdict

        by_ns = defaultdict(list)
        for entry in self._store.values():
            by_ns[entry.namespace].append(entry)

        removed = []
        for ns, entries in by_ns.items():
            for i, a in enumerate(entries):
                for b in entries[i + 1 :]:
                    if a.key == b.key:
                        continue
                    sim = self._similarity(a, b)
                    if sim > 0.90:
                        stronger = a if a.strength >= b.strength else b
                        weaker = b if a.strength >= b.strength else a
                        nk = f"{weaker.namespace}/{weaker.key}"
                        if nk in self._store:
                            del self._store[nk]
                            removed.append(
                                {
                                    "key": weaker.key,
                                    "namespace": weaker.namespace,
                                    "merged_into": stronger.key,
                                    "similarity": round(sim, 3),
                                }
                            )
        return removed

    def _similarity(self, a: MemoryEntry, b: MemoryEntry) -> float:
        text_a = json.dumps(a.value, default=str).lower() if a.value else ""
        text_b = json.dumps(b.value, default=str).lower() if b.value else ""
        if not text_a or not text_b:
            return 0.0
        words_a = set(text_a.split())
        words_b = set(text_b.split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union) if union else 0.0

    # ── stats ───────────────────────────────────────────────────────────────
    def stats(self) -> Dict:
        with self._lock:
            by_namespace = defaultdict(int)
            by_agent = defaultdict(int)
            total_strength = 0.0
            for entry in self._store.values():
                by_namespace[entry.namespace] += 1
                by_agent[entry.source_agent] += 1
                total_strength += entry.strength

            count = len(self._store)
            return {
                "total_memories": count,
                "avg_strength": round(total_strength / count, 4) if count else 0,
                "by_namespace": dict(by_namespace),
                "by_agent": dict(by_agent),
            }


# ── singleton ───────────────────────────────────────────────────────────────
_pool: Optional[SharedMemoryPool] = None


def get_shared_memory() -> SharedMemoryPool:
    global _pool
    if _pool is None:
        _pool = SharedMemoryPool()
    return _pool


def share_memory(
    agent_id: str, namespace: str, key: str, value: Any, tags: Optional[List[str]] = None,
    user_id: str = "local",
) -> Optional[MemoryEntry]:
    return get_shared_memory().write(agent_id, namespace, key, value, tags, user_id=user_id)


def recall_shared(
    agent_id: str, query: str, namespaces: Optional[List[str]] = None, limit: int = 10,
    user_id: str = "local",
) -> List[Dict]:
    return get_shared_memory().search(query, agent_id, namespaces, limit, user_id=user_id)


def cleanup_stale_memories() -> Dict:
    return get_shared_memory().cleanup_stale()


def get_memory_observation(entry_id: str, user_id: str = "local") -> Optional[Dict]:
    """Get full memory details by compact ID."""
    return get_shared_memory().get_observation(entry_id, user_id=user_id)


def get_memory_timeline(
    agent_id: str = "unknown", namespaces: Optional[List[str]] = None, limit: int = 20, descending: bool = True,
    user_id: str = "local",
) -> List[Dict]:
    """Get memories in chronological order."""
    return get_shared_memory().timeline(agent_id, namespaces, limit, descending, user_id=user_id)
