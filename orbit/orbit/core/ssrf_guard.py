# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
SSRF Guard — blocks outbound requests to private/internal IP ranges.

Protects against Server-Side Request Forgery where an attacker uses
ORBIT tools (web search, SEO, browser, n8n, firecrawl) to scan
internal network services.
"""

import ipaddress
import logging
import socket
import threading
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# RFC 1918 + loopback + link-local + broadcast + multicast
BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("10.0.0.0/8"),  # private Class A
    ipaddress.ip_network("172.16.0.0/12"),  # private Class B
    ipaddress.ip_network("192.168.0.0/16"),  # private Class C
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("0.0.0.0/8"),  # "this" network
]

# Allowlisted domains that are safe to access (external services)
ALLOWLIST_DOMAINS = {
    "openai.com",
    "api.openai.com",
    "openrouter.ai",
    "api.openrouter.ai",
    "googleapis.com",
    "generativelanguage.googleapis.com",
    "groq.com",
    "api.groq.com",
    "anthropic.com",
    "api.anthropic.com",
    "github.com",
    "api.github.com",
    "ollama.com",
    "registry.ollama.ai",
    "huggingface.co",
    "huggingface.co",
    "duckduckgo.com",
    "lite.duckduckgo.com",
    "127.0.0.1",
    "localhost",
}

# Known safe ports for localhost (ORBIT internal services)
LOCALHOST_ALLOWED_PORTS = {5000, 11434, 20128, 8082}


class SSRFGuard:
    """Validates outbound URLs against private IP ranges."""

    def __init__(self):
        self._dns_cache = {}
        self._lock = threading.Lock()
        self._stats = {"checked": 0, "blocked": 0}

    def is_safe_url(self, url: str) -> bool:
        """Return True if URL is safe to fetch (not targeting internal IPs)."""
        self._stats["checked"] += 1
        try:
            parsed = urlparse(url)
        except Exception:
            self._stats["blocked"] += 1
            return False

        # Only http/https
        if parsed.scheme not in ("http", "https"):
            self._stats["blocked"] += 1
            logger.warning("SSRF blocked non-HTTP scheme: %s", parsed.scheme)
            return False

        hostname = parsed.hostname
        if not hostname:
            self._stats["blocked"] += 1
            return False

        # Allowlist check (fast path)
        if self._is_allowlisted(hostname, parsed.port):
            return True

        # DNS resolution + IP check
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            for family, _, _, _, sockaddr in resolved:
                ip = ipaddress.ip_address(sockaddr[0])
                for net in BLOCKED_NETWORKS:
                    if ip in net:
                        self._stats["blocked"] += 1
                        logger.warning(
                            "SSRF blocked: %s resolves to %s (in %s)",
                            hostname,
                            ip,
                            net,
                        )
                        return False
        except (socket.gaierror, ValueError):
            # DNS resolution failed — block by default
            self._stats["blocked"] += 1
            logger.warning("SSRF blocked: cannot resolve %s", hostname)
            return False

        return True

    def _is_allowlisted(self, hostname: str, port: Optional[int]) -> bool:
        """Check if hostname is in the allowlist."""
        # Exact match
        if hostname in ALLOWLIST_DOMAINS:
            # For localhost/127.0.0.1, also check port
            if hostname in ("127.0.0.1", "localhost") and port not in LOCALHOST_ALLOWED_PORTS:
                return False
            return True
        # Suffix match (e.g., api.openai.com matches openai.com)
        for allowed in ALLOWLIST_DOMAINS:
            if hostname.endswith("." + allowed):
                return True
        return False

    def validate_outbound(self, url: str) -> Optional[str]:
        """Return error message if URL is unsafe, else None."""
        if not self.is_safe_url(url):
            return f"SSRF blocked: {url} targets internal/private network"
        return None

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self._stats)


_singleton: Optional[SSRFGuard] = None
_lock = threading.Lock()


def get_ssrf_guard() -> SSRFGuard:
    global _singleton
    if _singleton is None:
        with _lock:
            if _singleton is None:
                _singleton = SSRFGuard()
    return _singleton
