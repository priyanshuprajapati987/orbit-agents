# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
Circuit Breaker + Retry patterns for ORBIT
Three-state circuit breaker (CLOSED → OPEN → HALF_OPEN → CLOSED)
Retry with exponential backoff + jitter
"""

import asyncio
import collections
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 3
    consecutive_success_threshold: int = 2


@dataclass
class CircuitBreakerState:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    total_calls: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_errors: list[tuple[float, str]] = field(default_factory=lambda: collections.deque(maxlen=20))


class CircuitBreaker:
    def __init__(self, name: str, config: CircuitBreakerConfig | None = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState()
        self._lock = threading.Lock()  # Single lock for both sync and async paths

    @property
    def state(self) -> CircuitState:
        return self._state.state

    async def call(self, fn: Callable, *args, **kwargs) -> Any:
        with self._lock:
            if self._state.state == CircuitState.OPEN:
                if time.time() - self._state.last_failure_time >= self.config.recovery_timeout:
                    logger.info("Circuit %s: OPEN → HALF_OPEN (recovery timeout elapsed)", self.name)
                    self._state.state = CircuitState.HALF_OPEN
                    self._state.success_count = 0
                else:
                    raise CircuitBreakerOpenError(self.name, self._state.last_failure_time)

        try:
            result = await fn(*args, **kwargs) if asyncio.iscoroutinefunction(fn) else fn(*args, **kwargs)
        except Exception as e:
            await self._on_failure(e)
            raise

        # Providers return LLMResponse(success=False) instead of raising on
        # connection/API failures — count those as failures too, otherwise the
        # breaker never opens and a dead provider is retried on every call.
        if getattr(result, "success", True) is False:
            await self._on_failure(
                Exception(getattr(result, "error", None) or f"{self.name} provider returned success=False")
            )
            return result

        await self._on_success()
        return result

    async def _on_failure(self, error: Exception):
        with self._lock:
            self._state.failure_count += 1
            self._state.total_failures += 1
            self._state.total_calls += 1
            self._state.last_failure_time = time.time()
            self._state.last_errors.append((time.time(), str(error)[:200]))

            if self._state.state == CircuitState.HALF_OPEN:
                logger.warning("Circuit %s: HALF_OPEN → OPEN (call failed during recovery)", self.name)
                self._state.state = CircuitState.OPEN
            elif self._state.failure_count >= self.config.failure_threshold:
                logger.warning(
                    "Circuit %s: CLOSED → OPEN (threshold: %d failures)", self.name, self.config.failure_threshold
                )
                self._state.state = CircuitState.OPEN

    async def _on_success(self):
        with self._lock:
            self._state.success_count += 1
            self._state.total_successes += 1
            self._state.total_calls += 1
            self._state.last_success_time = time.time()

            if self._state.state == CircuitState.HALF_OPEN:
                if self._state.success_count >= self.config.consecutive_success_threshold:
                    logger.info("Circuit %s: HALF_OPEN → CLOSED (recovered)", self.name)
                    self._state.state = CircuitState.CLOSED
                    self._state.failure_count = 0
                    self._state.success_count = 0

    async def force_open(self):
        with self._lock:
            self._state.state = CircuitState.OPEN
            self._state.last_failure_time = time.time()

    async def force_close(self):
        with self._lock:
            self._state.state = CircuitState.CLOSED
            self._state.failure_count = 0
            self._state.success_count = 0

    def get_state(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.state.value,
            "failure_count": self._state.failure_count,
            "success_count": self._state.success_count,
            "total_calls": self._state.total_calls,
            "total_failures": self._state.total_failures,
            "total_successes": self._state.total_successes,
            "last_failure_time": self._state.last_failure_time,
            "last_success_time": self._state.last_success_time,
            "last_errors": list(self._state.last_errors)[-5:],
        }

    def _on_failure_sync(self, error: Exception):
        """Sync version of _on_failure for use without async context."""
        with self._lock:
            self._state.failure_count += 1
            self._state.total_failures += 1
            self._state.total_calls += 1
            self._state.last_failure_time = time.time()
            self._state.last_errors.append((time.time(), str(error)[:200]))
            if self._state.state == CircuitState.HALF_OPEN:
                self._state.state = CircuitState.OPEN
            elif self._state.failure_count >= self.config.failure_threshold:
                self._state.state = CircuitState.OPEN

    def _on_success_sync(self):
        """Sync version of _on_success for use without async context."""
        with self._lock:
            self._state.success_count += 1
            self._state.total_successes += 1
            self._state.total_calls += 1
            self._state.last_success_time = time.time()
            if self._state.state == CircuitState.HALF_OPEN:
                if self._state.success_count >= self.config.consecutive_success_threshold:
                    self._state.state = CircuitState.CLOSED
                    self._state.failure_count = 0
                    self._state.success_count = 0


class CircuitBreakerOpenError(Exception):
    def __init__(self, name: str, last_failure: float):
        self.circuit_name = name
        self.last_failure = last_failure
        super().__init__(f"Circuit breaker '{name}' is OPEN (last failure: {time.ctime(last_failure)})")


circuit_breakers: dict[str, CircuitBreaker] = {}
_circuit_breakers_lock = threading.Lock()


def get_circuit_breaker(name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
    with _circuit_breakers_lock:
        if name not in circuit_breakers:
            circuit_breakers[name] = CircuitBreaker(name, config)
        return circuit_breakers[name]


def get_all_circuit_states() -> list[dict]:
    return [cb.get_state() for cb in circuit_breakers.values()]


def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff: float = 2.0,
    jitter: float = 0.1,
    exceptions: tuple = (Exception,),
):
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return await fn(*args, **kwargs) if asyncio.iscoroutinefunction(fn) else fn(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt < max_retries:
                        delay = min(base_delay * (backoff**attempt), max_delay)
                        jitter_amount = delay * jitter * random.random()
                        total_delay = delay + jitter_amount
                        logger.warning(
                            "Retry %s attempt %d/%d failed: %s. Retrying in %.2fs",
                            fn.__name__,
                            attempt + 1,
                            max_retries,
                            e,
                            total_delay,
                        )
                        await asyncio.sleep(total_delay)
            raise last_error

        return wrapper

    return decorator
