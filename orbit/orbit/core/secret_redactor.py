# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Secret Redactor

Centralized secret redaction for logs, tool outputs, error messages and
audit events. Detects and masks API keys, bearer tokens, cookies,
passwords and common credential patterns so secrets never leak into
persistent logs.
"""

import re
import threading
from typing import Any, Dict, Optional

_MASK = "[REDACTED]"

_PATTERNS = [
    # OpenAI-style: sk-... / sk-proj-...
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    # Generic API key: word=key / word: key
    re.compile(r"\b(api[_-]?key|apikey)\b[\"':=\s]+[A-Za-z0-9_\-\.]{12,}", re.IGNORECASE),
    # Bearer / Basic tokens
    re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"\bBasic\s+[A-Za-z0-9+/]{16,}={0,2}", re.IGNORECASE),
    # Authorization headers
    re.compile(r"\b(x-api-key|authorization)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9\-._~+/]{8,}", re.IGNORECASE),
    # Password-like assignments
    re.compile(
        r"\b(password|passwd|pwd|secret|token|access_token|refresh_token|client_secret)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;{}]{6,}",
        re.IGNORECASE,
    ),
    # Anthropic: sk-ant-...
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
    # Groq: gsk_...
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    # Google API keys (39 chars, often AIza...)
    re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"),
    # AWS access key id
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # GitHub Personal Access Tokens (ghp_, gho_, ghu_, ghs_, ghr_)
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b"),
    # GitHub Fine-grained PATs (github_pat_)
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    # Private keys (common)
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    # jwt
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    # Email addresses (basic pattern)
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    # Slack tokens (xoxb-, xoxp-, xoxa-, xoxr-)
    re.compile(r"\bxox[bpars]-[0-9A-Za-z\-]{10,}\b"),
    # Stripe keys (sk_live_, pk_live_, sk_test_, pk_test_)
    re.compile(r"\b[sp]k_(?:live|test)_[0-9A-Za-z]{24,}\b"),
    # Discord bot tokens
    re.compile(r"\b[MB][A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"),
    # ORBIT's own tokens: orbit_<hex> API keys + per-boot UI tokens
    re.compile(r"\borbit_[A-Za-z0-9_-]{16,}\b"),
    # ORBIT session cookie values in free text (dict keys are covered
    # separately via _HEADER_KEYS; this catches log lines like
    # "_orbit_session=abc123; path=/").
    re.compile(r"_orbit_session\s*=\s*[A-Za-z0-9\-._~+/]+=*"),
]

_HEADER_KEYS = {
    "authorization",
    "x-api-key",
    "api-key",
    "apikey",
    "api_key",
    "orbit_api_key",
    "orbit-api-key",
    "secret_key",
    "x-auth-token",
    "x-access-token",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-rapidapi-key",
    "x-functions-key",
    "_orbit_session",
    "orbit_session",
}


def redact_value(value: Any) -> Any:
    """Redact strings recursively inside dicts/lists/strings."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and k.strip().lower() in _HEADER_KEYS:
                out[k] = _MASK
            else:
                out[k] = redact_value(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value


def redact_text(text: str) -> str:
    """Mask known credential patterns inside a string."""
    if not isinstance(text, str) or not text:
        return text
    redacted = text
    for pat in _PATTERNS:
        redacted = pat.sub(_MASK, redacted)
    return redacted


def redact_logging(record_attrs: Dict) -> Dict:
    """Redact structured log/audit payload keys before persistence."""
    return redact_value(record_attrs)


class SecretRedactor:
    """Singleton facade used by audit + policy engine."""

    _instance: Optional["SecretRedactor"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._redacted_count = 0
        self._patterns = _PATTERNS

    @classmethod
    def get_instance(cls) -> "SecretRedactor":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = SecretRedactor()
        return cls._instance

    def redact(self, text: str) -> str:
        out = redact_text(text)
        if out != text:
            with self._lock:
                self._redacted_count += 1
        return out

    def redact_dict(self, data: Dict) -> Dict:
        return redact_value(data)

    def stats(self) -> Dict:
        return {
            "redacted_items": self._redacted_count,
            "patterns": len(self._patterns),
        }

    def reset(self):
        self._redacted_count = 0


def get_redactor() -> SecretRedactor:
    return SecretRedactor.get_instance()
