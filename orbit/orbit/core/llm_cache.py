# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
LLM Response Cache — LRU cache with TTL for identical prompts
Reduces API costs, speeds up repeated queries, handles provider outages
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .llm.router import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

# Categories whose responses are worth caching vs stale-safe. Facts/search
# answers don't change; code/reasoning/creative are context-sensitive. Requests
# without an explicit category (None) are NOT cached: an unclassified query may
# be context-sensitive, so the stale-safe default is to skip the cache.
CACHE_BY_CATEGORY = {
    "facts": True,
    "search": True,
    "fast": True,
    "chat": True,
    "general": True,
    "code": False,
    "coding": False,
    "reasoning": False,
    "creative": False,
}


def is_cacheable(category: Optional[str]) -> bool:
    """Whether responses for a query category may be cached."""
    return CACHE_BY_CATEGORY.get(category, False) if category is not None else False


# Common stable facts — answered instantly from the seed (no LLM call). The
# prompt is normalized (lowercase, stripped) and must match exactly. Answers are
# terse canonical facts; the seed is a latency + cost quick win, not a full KB.
FACT_CACHE_SEED = {
    # Geography
    "capital of australia": "Canberra",
    "what is the capital of australia": "Canberra",
    "capital of japan": "Tokyo",
    "what is the capital of japan": "Tokyo",
    "capital of france": "Paris",
    "what is the capital of france": "Paris",
    "capital of india": "New Delhi",
    "what is the capital of india": "New Delhi",
    "capital of china": "Beijing",
    "capital of the united kingdom": "London",
    "capital of uk": "London",
    "capital of russia": "Moscow",
    "capital of canada": "Ottawa",
    "capital of brazil": "Brasilia",
    "capital of germany": "Berlin",
    "capital of italy": "Rome",
    "capital of spain": "Madrid",
    "capital of egypt": "Cairo",
    "capital of mexico": "Mexico City",
    "capital of the usa": "Washington, D.C.",
    "capital of usa": "Washington, D.C.",
    "capital of the united states": "Washington, D.C.",
    "largest planet in our solar system": "Jupiter",
    "what is the largest planet in our solar system": "Jupiter",
    "smallest planet in our solar system": "Mercury",
    "what is the smallest planet in our solar system": "Mercury",
    "largest ocean in the world": "Pacific Ocean",
    "what is the largest ocean in the world": "Pacific Ocean",
    "longest river in the world": "The Nile (by convention; the Amazon is longer by volume)",
    "what is the longest river in the world": "The Nile (by convention; the Amazon is longer by volume)",
    "highest mountain in the world": "Mount Everest",
    "what is the highest mountain in the world": "Mount Everest",
    "largest desert in the world": "Antarctica (polar desert); the largest hot desert is the Sahara",
    "what is the largest desert in the world": "Antarctica (polar desert); the largest hot desert is the Sahara",
    "largest country in the world by area": "Russia",
    "most populous country in the world": "India",
    "how many continents are there": "Seven",
    "how many countries are there in the world": "195 recognized sovereign states",
    "how many oceans are there": "Five (Pacific, Atlantic, Indian, Southern, Arctic)",
    # Science
    "what is the chemical formula for table salt": "NaCl",
    "chemical formula for table salt": "NaCl",
    "chemical formula for water": "H2O",
    "what is the chemical formula for water": "H2O",
    "chemical formula for carbon dioxide": "CO2",
    "what is the chemical symbol for gold": "Au",
    "chemical symbol for gold": "Au",
    "chemical symbol for iron": "Fe",
    "what is the chemical symbol for oxygen": "O",
    "how many bones does an adult human have": "206",
    "how many teeth does an adult human have": "32",
    "what is the boiling point of water in celsius": "100 degrees Celsius",
    "what is the freezing point of water in celsius": "0 degrees Celsius",
    "what is the speed of light in meters per second": "About 299,792,458 m/s",
    "speed of light in meters per second": "About 299,792,458 m/s",
    "what is the speed of sound in air": "About 343 m/s at 20 degrees Celsius",
    "what is the earth's diameter": "About 12,742 km",
    "diameter of the earth": "About 12,742 km",
    "what is the distance from the earth to the moon": "About 384,400 km",
    "distance from the earth to the moon": "About 384,400 km",
    "how many planets are in our solar system": "Eight",
    "how many moons does the earth have": "One",
    "what planet is known as the red planet": "Mars",
    "what is the force that keeps planets in orbit": "Gravity",
    "who discovered gravity": "Isaac Newton",
    "who invented the light bulb": "Thomas Edison is widely credited, though earlier inventors worked on it",
    "who discovered penicillin": "Alexander Fleming",
    "who discovered the theory of relativity": "Albert Einstein",
    "who developed the theory of evolution": "Charles Darwin",
    "who invented the telephone": "Alexander Graham Bell",
    "who invented the first printing press": "Johannes Gutenberg",
    "what is the powerhouse of the cell": "The mitochondria",
    "what gas do plants absorb": "Carbon dioxide",
    "what gas do humans breathe out": "Carbon dioxide",
    "how many chromosomes do humans have": "46",
    "what is the largest organ of the human body": "The skin",
    "largest organ of the human body": "The skin",
    # History / People
    "who painted the mona lisa": "Leonardo da Vinci",
    "who painted starry night": "Vincent van Gogh",
    "who was the first person on the moon": "Neil Armstrong",
    "who was the first president of the united states": "George Washington",
    "first president of the united states": "George Washington",
    "when did world war 2 end": "September 2, 1945",
    "when did world war i start": "July 28, 1914",
    "when was the declaration of independence signed": "July 4, 1776",
    "who wrote romeo and juliet": "William Shakespeare",
    "who wrote hamlet": "William Shakespeare",
    "who wrote the gita": "Vyasa (traditionally attributed)",
    "who wrote the declaration of independence": "Thomas Jefferson (primary author)",
    "who was the first woman to fly solo across the atlantic": "Amelia Earhart",
    # Math / Numbers
    "what is 15% of 240": "36",
    "what is the square root of 144": "12",
    "what is pi to 5 decimal places": "3.14159",
    "how many seconds are in a minute": "60",
    "how many seconds in an hour": "3,600",
    "how many minutes in a day": "1,440",
    "how many days in a year": "365",
    "how many days in a leap year": "366",
    "how many hours in a day": "24",
    "what is 2 to the power of 10": "1,024",
    "what is 10 factorial": "3,628,800",
    "what is 0 multiplied by 5": "0",
    "what is the value of pi": "About 3.14159",
    "what is 7 times 8": "56",
    "what is 12 times 12": "144",
    # Sports / Entertainment / Misc
    "how many players are on a football team": "11",
    "how many players in a soccer team": "11",
    "how many players in a basketball team": "5",
    "how many olympic rings are there": "5",
    "how many letters are in the english alphabet": "26",
    "how many colors are in a rainbow": "7",
    "how many days are in february in a leap year": "29",
    "how many weeks in a year": "52",
    "how many zeros in a million": "6",
    "how many zeros in a billion": "9",
    "what is the largest mammal in the world": "The blue whale",
    "largest mammal in the world": "The blue whale",
    "what is the fastest land animal": "The cheetah",
    "fastest land animal": "The cheetah",
    "what is the tallest animal in the world": "The giraffe",
    "tallest animal in the world": "The giraffe",
    "how many legs does a spider have": "8",
    "how many legs does an insect have": "6",
    "what is the national animal of the usa": "The bald eagle",
    "how many keys does a piano have": "88",
    "how many strings does a guitar have": "6",
    "how many minutes are in an hour": "60",
}


@dataclass
class CacheEntry:
    response: LLMResponse
    timestamp: float
    hits: int = 0


class LLMResponseCache:
    def __init__(self, max_size: int = 500, default_ttl: float = 300.0):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0
        self._seed_hits = 0
        self._lock = threading.Lock()
        self._miss_set: set[str] = set()
        self._miss_set_max = 10000
        self._inflight: dict[str, tuple[int, Any, float]] = {}
        self._key_cache: dict[str, str] = {}
        self._key_cache_max = 1000

    def _make_key(self, request: LLMRequest) -> str:
        fast_key = (
            request.prompt,
            request.model,
            request.system_prompt,
            request.force_offline,
            request.max_tokens,
            request.use_cache,
            str(request.history) if request.history else "",
            str(request.extra_context) if request.extra_context else "",
        )
        with self._lock:
            cached = self._key_cache.get(fast_key)
            if cached is not None:
                return cached
        raw = json.dumps(
            {
                "p": request.prompt,
                "m": request.model,
                "s": request.system_prompt,
                "f": request.force_offline,
                "t": request.max_tokens,
                "u": request.use_cache,
                "h": request.history,
                "e": request.extra_context,
            },
            sort_keys=True,
            default=str,
        )
        key = hashlib.sha256(raw.encode()).hexdigest()[:32]
        with self._lock:
            if len(self._key_cache) < self._key_cache_max:
                self._key_cache[fast_key] = key
        return key

    def get(self, request: LLMRequest) -> Optional[LLMResponse]:
        seed = self._seed_get(request)
        if seed is not None:
            return seed
        key = self._make_key(request)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                self._miss_set.add(key)
                if len(self._miss_set) > self._miss_set_max:
                    # Prune oldest half
                    for _k in list(self._miss_set)[: len(self._miss_set) // 2]:
                        self._miss_set.discard(_k)
                return None
            # Expired: evict immediately
            if time.time() - entry.timestamp > self._default_ttl:
                del self._cache[key]
                self._miss_set.discard(key)
                self._misses += 1
                return None
            entry.hits += 1
            self._cache.move_to_end(key)
            self._miss_set.discard(key)
            self._hits += 1
            return entry.response

    def _seed_get(self, request: LLMRequest) -> Optional[LLMResponse]:
        """Instant canonical answer for an exact seed-prompt match (no LLM call)."""
        if request.history or request.extra_context or not request.prompt:
            return None
        answer = FACT_CACHE_SEED.get(request.prompt.strip().lower())
        if not answer:
            return None
        from .llm.router import LLMResponse

        with self._lock:
            self._seed_hits += 1
        return LLMResponse(
            content=answer,
            provider="seed",
            model="static-facts",
            latency_ms=0,
            success=True,
            used_cache=True,
        )

    def set(self, request: LLMRequest, response: LLMResponse):
        key = self._make_key(request)
        with self._lock:
            if response and response.success:
                self._cache[key] = CacheEntry(response=response, timestamp=time.time())
                self._miss_set.discard(key)
                while len(self._cache) > self._max_size:
                    self._cache.popitem(last=False)
            # Always wake singleflight waiters, even on failure, so they
            # re-check (and compute themselves) instead of timing out.
            self._finish_inflight_locked(key)
            self._finish_inflight_locked(key)

    def _finish_inflight_locked(self, key: str) -> None:
        """Wake singleflight waiters. Assumes self._lock is held."""
        entry = self._inflight.pop(key, None)
        if entry is not None:
            _, event, _born = entry
            event.set()

    def begin_miss(self, request: LLMRequest, timeout: float = 60.0) -> tuple[bool, Optional[LLMResponse]]:
        """Singleflight for cache misses (thundering-herd guard).

        Returns (is_owner, response). The first misser becomes the owner
        (True, None) and must compute + set() (which wakes waiters).
        Concurrent missers block up to `timeout` for the owner's result,
        then re-read the cache; on timeout they get (False, None) and
        should compute themselves.
        """
        import threading as _threading

        key = self._make_key(request)
        with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                _depth, event, born = existing
                # Owner died without set(): evict stale claims (>120 s)
                # instead of making every future misser wait it out.
                if time.time() - born >= 120:
                    del self._inflight[key]
                    existing = None
            if existing is None:
                self._inflight[key] = (0, _threading.Event(), time.time())
                return True, None
            _depth, event, _born = existing
        fired = event.wait(timeout)
        if not fired:
            return False, None
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or time.time() - entry.timestamp > self._default_ttl:
                return False, None
            entry.hits += 1
            self._cache.move_to_end(key)
            self._hits += 1
            return False, entry.response

    async def aclaim_miss(self, request: LLMRequest, timeout: float = 60.0) -> tuple[bool, Optional[LLMResponse]]:
        """Async singleflight for the router's generate path (never blocks
        the event loop; waiters suspend on asyncio.Event). Same contract
        as begin_miss: (is_owner, shared_response_or_None)."""
        import asyncio as _asyncio

        key = self._make_key(request)
        with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                _depth, _ev, born = existing
                if time.time() - born >= 120:
                    del self._inflight[key]
                    existing = None
            if existing is None:
                self._inflight[key] = (0, _asyncio.Event(), time.time())
                return True, None
            _depth, event, _born = existing
        try:
            await _asyncio.wait_for(event.wait(), timeout)
        except _asyncio.TimeoutError:
            return False, None
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or time.time() - entry.timestamp > self._default_ttl:
                return False, None
            entry.hits += 1
            self._cache.move_to_end(key)
            self._hits += 1
            return False, entry.response

    def invalidate(self, request: LLMRequest):
        key = self._make_key(request)
        with self._lock:
            self._cache.pop(key, None)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._miss_set.clear()
            self._key_cache.clear()
            for _key, (_depth, event, _born) in self._inflight.items():
                try:
                    event.set()
                except Exception:
                    pass
            self._inflight.clear()
            self._hits = 0
            self._misses = 0
            self._seed_hits = 0

    def get_stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = round(self._hits / total * 100, 1) if total > 0 else 0
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate_pct": hit_rate,
                "seed_hits": self._seed_hits,
                "default_ttl_s": self._default_ttl,
                "miss_set_size": len(self._miss_set),
                "key_cache_size": len(self._key_cache),
            }


_cache: LLMResponseCache | None = None
_cache_lock = threading.Lock()


def get_cache() -> LLMResponseCache:
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = LLMResponseCache()
    return _cache
