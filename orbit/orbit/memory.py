# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Conversation Memory Module

This module manages conversation history storage:

Storage:
- JSON file-based (orbit_memory.json)
- Thread-safe with locking
- Automatic pruning (max 100 entries, 7 days)

Key Functions:
    save(role, text) - Save a conversation entry
    get_recent(n) - Get recent conversation history
    search(query) - Search conversation history
    clear() - Clear all memory

Usage:
    from orbit.memory import save, get_recent

    save("user", "Hello!")
    save("assistant", "Hi! How can I help?")

    history = get_recent(10)
"""

from .core.runtime_paths import orbit_data_dir
import json
import threading
from datetime import datetime

from .logging_config import get_logger
from .core.memory_safety import sanitize_recalled_memory

logger = get_logger(__name__)


def _sanitize_item(item: dict) -> dict:
    """Return a copy of a memory entry with recalled text stripped of
    instruction-injection markers (memory poisoning defense)."""
    clean = dict(item)
    if clean.get("user"):
        clean["user"] = sanitize_recalled_memory(clean["user"])
    if clean.get("assistant"):
        clean["assistant"] = sanitize_recalled_memory(clean["assistant"])
    return clean


MEMORY_FILE = orbit_data_dir() / "orbit_memory.json"
MAX_MEMORIES = 100
MAX_AGE_DAYS = 7
_lock = threading.Lock()


def _load():
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save(data: list):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMORY_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data[-MAX_MEMORIES:], f, indent=2, ensure_ascii=False)
    tmp.replace(MEMORY_FILE)


def _is_older_than(item, days: int) -> bool:
    try:
        ts = item.get("timestamp", "")
        item_time = datetime.fromisoformat(ts).timestamp()
        cutoff = datetime.now().timestamp() - (days * 86400)
        return item_time < cutoff
    except (ValueError, TypeError):
        return False


def count_pending_cleanup() -> int:
    with _lock:
        data = _load()
    return sum(1 for item in data if _is_older_than(item, MAX_AGE_DAYS))


def get_pending_cleanup() -> list:
    with _lock:
        data = _load()
    return [item for item in data if _is_older_than(item, MAX_AGE_DAYS)]


def apply_cleanup():
    with _lock:
        data = _load()
        kept = [item for item in data if not _is_older_than(item, MAX_AGE_DAYS)]
        if len(kept) < len(data):
            _save(kept)
            logger.info("Memory cleanup applied", extra={"removed": len(data) - len(kept), "remaining": len(kept)})
            return len(data) - len(kept)
    logger.debug("Memory cleanup: nothing to remove")
    return 0


def save_conversation(user_text: str, assistant_text: str, context: str = ""):
    with _lock:
        data = _load()
        data.append(
            {
                "timestamp": datetime.now().isoformat(),
                "user": user_text,
                "assistant": assistant_text,
                "context": context,
            }
        )
        _save(data)
    logger.debug(
        "Conversation saved",
        extra={"context": context, "user_length": len(user_text), "assistant_length": len(assistant_text)},
    )


def get_all():
    with _lock:
        data = _load()
    logger.debug("Memory loaded", extra={"count": len(data)})
    return data


def get_recent(n: int = 10):
    with _lock:
        data = _load()[-n:]
    logger.debug("Recent memories retrieved", extra={"count": len(data)})
    return [_sanitize_item(item) for item in data]


def set_max_age(days: int):
    global MAX_AGE_DAYS
    MAX_AGE_DAYS = max(1, days)
    logger.info("Memory max age updated", extra={"days": MAX_AGE_DAYS})


def search(query: str, limit: int = 5):
    with _lock:
        data = _load()
    query = query.lower()
    results = []
    for item in reversed(data):
        user_text = item.get("user", "")
        assistant_text = item.get("assistant", "")
        if query in user_text.lower() or query in assistant_text.lower():
            results.append(_sanitize_item(item))
            if len(results) >= limit:
                break
    logger.debug("Memory search", extra={"query": query, "results": len(results)})
    return results


def clear():
    with _lock:
        _save([])
    logger.info("Memory cleared")


def get_context_str(limit: int = 5) -> str:
    recent = get_recent(limit)
    if not recent:
        return ""
    lines = []
    for m in recent:
        # Mark recalled memory as inert data so it cannot act as a prompt instruction.
        lines.append(f"User: {sanitize_recalled_memory(m.get('user', ''))}")
        lines.append(f"ORBIT: {sanitize_recalled_memory(m.get('assistant', ''))}")
    logger.debug("Context string generated", extra={"entries": len(recent)})
    return "\n".join(lines)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 3-LAYER MEMORY PATTERN
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def _entry_id(entry: dict) -> str:
    """Generate a stable short ID for a memory entry."""
    import hashlib

    raw = f"{entry.get('timestamp', '')}{entry.get('user', '')}{entry.get('assistant', '')}"
    return hashlib.md5(raw.encode()).hexdigest()[:8]


def search_compact(query: str, limit: int = 10) -> list:
    """Layer 1 â€” Quick scan. Returns compact IDs + brief snippets only.

    Usage:
        ids = search_compact("python")  # [{id, snippet, ts}, ...]
        full = get_observations(ids)    # Layer 3 â€” get full details
    """
    with _lock:
        data = _load()
    query_lower = query.lower()
    results = []
    for item in reversed(data):
        user_text = item.get("user", "")
        assistant_text = item.get("assistant", "")
        if query_lower in user_text.lower() or query_lower in assistant_text.lower():
            entry_id = _entry_id(item)
            snippet = (
                sanitize_recalled_memory(user_text)[:60] + " -> " + sanitize_recalled_memory(assistant_text)[:60]
            ).strip()
            results.append(
                {
                    "id": entry_id,
                    "snippet": snippet,
                    "timestamp": item.get("timestamp", ""),
                }
            )
            if len(results) >= limit:
                break
    logger.debug("Memory search_compact", extra={"query": query, "results": len(results)})
    return results


def timeline(limit: int = 20, offset: int = 0) -> list:
    """Layer 2 â€” Chronological browse with pagination.

    Usage:
        page1 = timeline(limit=20, offset=0)
        page2 = timeline(limit=20, offset=20)
    """
    with _lock:
        data = _load()
    # Chronological order (oldest first)
    page = data[offset : offset + limit]
    results = []
    for item in page:
        clean = _sanitize_item(item)
        results.append(
            {
                "id": _entry_id(item),
                "timestamp": clean.get("timestamp", ""),
                "user": clean.get("user", "")[:100],
                "assistant": clean.get("assistant", "")[:100],
            }
        )
    logger.debug("Memory timeline", extra={"offset": offset, "limit": limit, "returned": len(results)})
    return results


def get_observations(entry_ids: list) -> list:
    """Layer 3 â€” Full detail for specific entry IDs.

    Usage:
        compact = search_compact("python")  # Layer 1
        ids = [e["id"] for e in compact]
        full = get_observations(ids)         # Layer 3 â€” full details
    """
    with _lock:
        data = _load()
    id_set = set(entry_ids)
    results = []
    for item in data:
        if _entry_id(item) in id_set:
            clean = _sanitize_item(item)
            results.append(
                {
                    "id": _entry_id(item),
                    "timestamp": clean.get("timestamp", ""),
                    "user": clean.get("user", ""),
                    "assistant": clean.get("assistant", ""),
                    "context": sanitize_recalled_memory(clean.get("context", "")),
                }
            )
    logger.debug("Memory get_observations", extra={"requested": len(entry_ids), "found": len(results)})
    return results
