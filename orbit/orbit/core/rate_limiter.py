# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import time
import threading
import logging
from functools import wraps
from flask import request, jsonify

logger = logging.getLogger(__name__)


class TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self.last_used = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: float = 1.0) -> bool:
        # Negative tokens would MINT credit (5 -> burst cap). Clamp first.
        try:
            tokens = max(0.0, float(tokens))
        except (TypeError, ValueError):
            return False
        with self._lock:
            now = time.monotonic()
            self.last_used = now
            elapsed = now - self.last_refill
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False


class RateLimiterRegistry:
    IDLE_TIMEOUT = 3600.0
    MAX_SCOPED = 2000

    def __init__(self):
        self._buckets: dict[str, TokenBucket] = {}
        self._scoped: set[str] = set()
        self._lock = threading.Lock()

    def register(self, name: str, rate: float, burst: int):
        with self._lock:
            self._buckets[name] = TokenBucket(rate=rate, burst=burst)

    def register_endpoint(self, endpoint: str, rate: float = 10.0, burst: int = 20):
        self.register(f"endpoint:{endpoint}", rate=rate, burst=burst)

    def check(self, name: str, tokens: float = 1.0) -> bool:
        bucket = self._buckets.get(name)
        if bucket is None:
            return True
        return bucket.consume(tokens)

    def scoped_check(self, base: str, scope: str, tokens: float = 1.0) -> bool:
        """Rate-limit per `scope` (e.g. client IP) under a registered base bucket.

        Lazily creates per-scope buckets inheriting the base bucket's rate/burst,
        so `base` stays a single configured template and `scope` scales per client.
        Stale per-scope buckets are pruned to keep the registry bounded.
        """
        with self._lock:
            base_bucket = self._buckets.get(base)
            if base_bucket is None:
                return True
            bucket_name = f"{base}:{scope}"
            bucket = self._buckets.get(bucket_name)
            if bucket is None:
                if len(self._scoped) >= self.MAX_SCOPED:
                    self._prune_scoped_locked()
                bucket = TokenBucket(rate=base_bucket.rate, burst=base_bucket.burst)
                self._buckets[bucket_name] = bucket
                self._scoped.add(bucket_name)
        return bucket.consume(tokens)

    def user_check(self, base: str, user_id: str, tokens: float = 1.0) -> bool:
        """Rate-limit per authenticated user under a registered base bucket.

        Uses the user_id as scope so each user gets their own bucket.
        Falls back to allowing the request if the base bucket is not registered.
        """
        if not user_id:
            return True
        return self.scoped_check(base, f"user:{user_id}", tokens=tokens)

    def _prune_scoped_locked(self, force: bool = False):
        """Drop idle per-scope buckets (or all of them when over the cap)."""
        now = time.monotonic()
        for name in list(self._scoped):
            bucket = self._buckets.get(name)
            if bucket is None:
                self._scoped.discard(name)
            elif force or (now - bucket.last_used) > self.IDLE_TIMEOUT:
                self._buckets.pop(name, None)
                self._scoped.discard(name)

    def get_status(self) -> dict:
        status = {}
        with self._lock:
            buckets_snapshot = dict(self._buckets)
        for name, bucket in buckets_snapshot.items():
            status[name] = {
                "tokens": round(bucket.tokens, 1),
                "burst": bucket.burst,
                "rate": bucket.rate,
                "available_pct": round(bucket.tokens / bucket.burst * 100, 1) if bucket.burst > 0 else 0,
            }
        return status

    def acquire(self, name: str, tokens: float = 1.0) -> bool:
        """Acquire a token from the rate limiter bucket. Registers lazily if needed."""
        with self._lock:
            if name not in self._buckets:
                self._buckets[name] = TokenBucket(rate=10.0, burst=20)
        return self.check(name, tokens)

    def can_proceed(self, name: str, tokens: float = 1.0) -> tuple[bool, dict]:
        """Check rate limit and return (allowed, details) tuple. Registers lazily if needed."""
        with self._lock:
            if name not in self._buckets:
                self._buckets[name] = TokenBucket(rate=10.0, burst=20)
        allowed = self.check(name, tokens)
        bucket = self._buckets.get(name)
        info = {
            "tokens": round(bucket.tokens, 1) if bucket else 0.0,
            "burst": bucket.burst if bucket else 0,
            "rate": bucket.rate if bucket else 0.0,
        }
        return allowed, info

    def get_usage(self, name: str) -> dict:
        """Get usage details for a specific bucket/key. Registers lazily if needed."""
        bucket = self._buckets.get(name)
        if bucket is None:
            self.register(name, rate=10.0, burst=20)
            bucket = self._buckets.get(name)
        return {
            "tokens": round(bucket.tokens, 1) if bucket else 0.0,
            "burst": bucket.burst if bucket else 0,
            "rate": bucket.rate if bucket else 0.0,
        }


_registry: RateLimiterRegistry | None = None
_lock = threading.Lock()


def get_limiter() -> RateLimiterRegistry:
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                _registry = RateLimiterRegistry()
    return _registry


def get_rate_limiter() -> RateLimiterRegistry:
    return get_limiter()


def rate_limit(name: str | None = None, rate: float = 10.0, burst: int = 20):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            key = name or f"endpoint:{request.endpoint}"
            limiter = get_limiter()
            if not limiter.check(key):
                logger.warning("Rate limit exceeded for %s (client: %s)", key, request.remote_addr)
                return jsonify({"error": "rate_limit_exceeded", "message": "Too many requests"}), 429
            return f(*args, **kwargs)

        return wrapper

    return decorator
