# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Security Hardening — API auth, rate limiting, input validation, audit

- API key authentication for all endpoints
- Rate limiting per IP
- Input sanitization
- CORS restrictions
- Request signing for mobile
- Security headers
- Audit logging
"""

from .runtime_paths import data_dir
import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass

from ..logging_config import get_logger

logger = get_logger(__name__)

DATA_DIR = data_dir() / "security"


@dataclass
class RateLimitEntry:
    count: int = 0
    window_start: float = 0.0


class SecurityHardening:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._api_keys: Dict[str, str] = {}
        self._rate_limits: Dict[str, RateLimitEntry] = {}
        self._blocked_ips: Set[str] = set()
        self._audit_log: List[Dict] = []
        self._config = {
            "rate_limit_per_minute": 60,
            "rate_limit_per_hour": 1000,
            "max_request_size_mb": 10,
            "require_api_key": True,
            "cors_origins": ["http://localhost:5173", "http://localhost:3000"],
            "enable_request_signing": False,
        }
        self._load_config()
        self._generate_default_key()

    def _path(self) -> Path:
        return DATA_DIR / "security_config.json"

    def _audit_path(self) -> Path:
        return DATA_DIR / "audit.json"

    def _load_config(self):
        p = self._path()
        if p.exists():
            try:
                saved = json.loads(p.read_text(encoding="utf-8"))
                self._config.update(saved)
            except Exception as exc:
                logger.warning("Corrupted security config, using defaults: %s", exc)

    def _save_config(self):
        self._path().write_text(json.dumps(self._config, indent=2), encoding="utf-8")

    def _generate_default_key(self):
        key_path = DATA_DIR / "api_keys.json"
        if key_path.exists():
            try:
                self._api_keys = json.loads(key_path.read_text(encoding="utf-8"))
            except Exception:
                self._api_keys = {}
        if not self._api_keys:
            default_key = f"orbit_{secrets.token_hex(16)}"
            self._api_keys["default"] = default_key
            key_path.write_text(json.dumps(self._api_keys, indent=2), encoding="utf-8")
            logger.info("Generated default API key: [REDACTED]")

    def _save_keys(self):
        key_path = DATA_DIR / "api_keys.json"
        key_path.write_text(json.dumps(self._api_keys, indent=2), encoding="utf-8")

    def verify_api_key(self, key: str) -> bool:
        if not self._config.get("require_api_key"):
            return True
        with self._lock:
            keys = list(self._api_keys.values())
        return key in keys

    def generate_api_key(self, name: str) -> str:
        key = f"orbit_{secrets.token_hex(16)}"
        with self._lock:
            self._api_keys[name] = key
            self._save_keys()
        return key

    def revoke_api_key(self, name: str) -> bool:
        with self._lock:
            if name in self._api_keys:
                del self._api_keys[name]
                self._save_keys()
                return True
            return False

    def list_api_keys(self) -> List[Dict]:
        with self._lock:
            items = list(self._api_keys.items())
        return [{"name": k, "key_preview": v[:12] + "..."} for k, v in items]

    def check_rate_limit(self, ip: str) -> bool:
        now = time.time()
        minute_key = f"{ip}:min"
        hour_key = f"{ip}:hour"

        with self._lock:
            # TTL prune: drop windows that already expired so the dict can't
            # grow unboundedly (one entry per IP per window).
            for key in list(self._rate_limits.keys()):
                entry = self._rate_limits.get(key)
                if entry is None:
                    continue
                window = 60 if key.endswith(":min") else 3600
                if now - entry.window_start > window:
                    del self._rate_limits[key]
            if len(self._rate_limits) > 20000:
                # hard cap under sustained cardinality attack; oldest first
                for key in list(self._rate_limits.keys())[: len(self._rate_limits) - 20000]:
                    del self._rate_limits[key]
            for key in [minute_key, hour_key]:
                entry = self._rate_limits.get(key)
                if entry is None:
                    self._rate_limits[key] = RateLimitEntry(count=1, window_start=now)
                    continue
                window = 60 if ":min" in key else 3600
                if now - entry.window_start > window:
                    entry.count = 1
                    entry.window_start = now
                else:
                    entry.count += 1
                limit = self._config["rate_limit_per_minute"] if ":min" in key else self._config["rate_limit_per_hour"]
                if entry.count > limit:
                    return False
        return True

    def is_ip_blocked(self, ip: str) -> bool:
        with self._lock:
            return ip in self._blocked_ips

    def block_ip(self, ip: str):
        with self._lock:
            self._blocked_ips.add(ip)
        self._log_audit("ip_blocked", {"ip": ip})

    def unblock_ip(self, ip: str):
        with self._lock:
            self._blocked_ips.discard(ip)

    def sanitize_input(self, text: str) -> str:
        import re

        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"javascript:", "", text, flags=re.IGNORECASE)
        text = re.sub(r"on\w+\s*=", "", text, flags=re.IGNORECASE)
        text = text.replace("\x00", "")
        return text

    def sign_request(self, payload: str, secret: str) -> str:
        return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def verify_signature(self, payload: str, signature: str, secret: str) -> bool:
        expected = self.sign_request(payload, secret)
        return hmac.compare_digest(expected, signature)

    def get_security_headers(self) -> Dict[str, str]:
        return {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com data:; "
                "img-src 'self' data: blob: https:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            ),
            "Referrer-Policy": "strict-origin-when-cross-origin",
        }

    def _log_audit(self, event: str, details: Optional[Dict] = None):
        entry = {
            "event": event,
            "details": details or {},
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            self._audit_log.append(entry)
            if len(self._audit_log) > 1000:
                self._audit_log = self._audit_log[-1000:]

    def get_audit_log(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            return list(reversed(self._audit_log[-limit:]))

    def get_config(self) -> Dict:
        return dict(self._config)

    def update_config(self, updates: Dict) -> Dict:
        with self._lock:
            self._config.update(updates)
            cfg = dict(self._config)
        self._save_config()
        return cfg

    def get_status(self) -> Dict:
        with self._lock:
            return {
                "api_keys": len(self._api_keys),
                "blocked_ips": len(self._blocked_ips),
                "audit_events": len(self._audit_log),
                "require_api_key": self._config.get("require_api_key", False),
                "rate_limit_per_minute": self._config.get("rate_limit_per_minute", 60),
            }


_instance: Optional[SecurityHardening] = None


def get_security_hardening() -> SecurityHardening:
    global _instance
    if _instance is None:
        _instance = SecurityHardening()
    return _instance
