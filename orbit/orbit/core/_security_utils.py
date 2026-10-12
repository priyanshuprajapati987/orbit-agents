# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""Shared security validation utilities for Flask blueprints.

Provides path traversal protection and SSRF prevention for all API endpoints.
"""

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse

from ..logging_config import get_logger

logger = get_logger(__name__)

# ── Path validation ──────────────────────────────────────────────────

BLOCKED_PATH_PARTS = frozenset(
    {
        "windows",
        "system32",
        "syswow64",
        "boot",
        "$recycle.bin",
        "programdata",
        "appdata",
    }
)


def _get_workspace() -> Path:
    # Single canonical root: delegate to ToolGate's resolver so every gate
    # enforces the same boundary (previously this defaulted to repo-root
    # via a different path computation, and RAG used yet another).
    from .policy_engine import get_workspace_root

    return get_workspace_root()


def validate_path(path: str, mutate: bool = False) -> tuple[bool, str]:
    """Validate a file path against the workspace sandbox.

    Args:
        path: The user-provided file path.
        mutate: True for write/delete operations, False for reads.

    Returns:
        (allowed, reason) tuple.
    """
    if not path:
        return False, "empty_path"

    try:
        resolved = Path(os.path.realpath(path)).resolve()
    except Exception:
        return False, "path_resolve_failed"

    # Always block system directories
    parts = {p.lower() for p in resolved.parts}
    if parts & BLOCKED_PATH_PARTS:
        return False, "blocked_system_directory"

    if not mutate:
        return True, "read_allowed"

    # For mutations, must be inside workspace
    workspace = _get_workspace()
    try:
        if workspace == resolved or workspace in resolved.parents:
            return True, "inside_workspace"
    except Exception:
        pass

    # Check extra workspaces
    extra = os.environ.get("ORBIT_EXTRA_WORKSPACES", "").strip()
    if extra:
        for ws_str in extra.split(";"):
            ws_str = ws_str.strip()
            if ws_str:
                try:
                    ws = Path(ws_str).resolve()
                    if ws == resolved or ws in resolved.parents:
                        return True, "inside_extra_workspace"
                except Exception:
                    continue

    return False, "outside_workspace"


def sanitize_filepath(filepath: str) -> str | None:
    """Reject paths with traversal components. Returns None if suspicious."""
    if not filepath:
        return None
    # Block obvious traversal attempts
    normalized = filepath.replace("\\", "/")
    if ".." in normalized.split("/"):
        return None
    return filepath


def confine_result_path(filepath: str | None, default: Path) -> Path | None:
    """Engine-level guard for result/export filepaths.

    HTTP blueprints validate, but engines are also reached via CLI and
    internal callers. A bare Path(filepath) + mkdir -p + write is an
    arbitrary-file-write primitive. Returns the resolved path confined
    to the workspace or the OS temp dir, or None to refuse.
    """
    import tempfile

    if not filepath:
        return default
    clean = sanitize_filepath(str(filepath))
    if clean is None:
        return None
    try:
        real = Path(clean).expanduser().resolve()
    except Exception:
        return None
    allowed, _reason = validate_path(clean, mutate=True)
    if allowed:
        return real
    try:
        tmp = Path(tempfile.gettempdir()).resolve()
        if real == tmp or tmp in real.parents:
            return real
    except Exception:
        pass
    return None


# ── URL validation (SSRF prevention) ────────────────────────────────

BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "metadata.google.internal",
        "instance-data",
        "169.254.169.254",
    }
)

BLOCKED_SCHEMES = frozenset({"file", "ftp", "data", "javascript", "vbscript"})

PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def validate_url(url: str, allow_private: bool = False) -> tuple[bool, str]:
    """Validate a URL against SSRF attacks.

    Args:
        url: The user-provided URL.
        allow_private: If True, allow private/network-local IPs (for dev).

    Returns:
        (allowed, reason) tuple.
    """
    if not url:
        return False, "empty_url"

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "url_parse_failed"

    # Block dangerous schemes
    scheme = parsed.scheme.lower()
    if scheme in BLOCKED_SCHEMES:
        return False, f"blocked_scheme_{scheme}"

    if scheme not in ("http", "https"):
        return False, "only_http_https_allowed"

    hostname = parsed.hostname or ""
    if not hostname:
        return False, "no_hostname"

    # Block known metadata/internal hosts
    if hostname.lower() in BLOCKED_HOSTS:
        return False, "blocked_host"

    # Block private IPs unless explicitly allowed
    if not allow_private:
        try:
            ip = ipaddress.ip_address(hostname)
            for net in PRIVATE_NETWORKS:
                if ip in net:
                    return False, "private_network_ip"
        except ValueError:
            # hostname is a domain name, not an IP — check for internal patterns
            if hostname.endswith(".local") or hostname.endswith(".internal"):
                return False, "internal_domain"

    return True, "url_allowed"


def validate_command(command: str, allowed_commands: set[str] | None = None) -> tuple[bool, str]:
    """Validate a mobile command against an allowlist.

    Args:
        command: The command string.
        allowed_commands: Set of allowed command names. If None, uses default.

    Returns:
        (allowed, reason) tuple.
    """
    if not command:
        return False, "empty_command"

    if allowed_commands is None:
        allowed_commands = {
            "status",
            "screenshot",
            "clipboard",
            "notification",
            "navigate",
            "scroll",
            "click",
            "type",
            "key",
            "brightness",
            "volume",
            "wifi",
            "bluetooth",
        }

    cmd = command.strip().lower()
    if cmd not in allowed_commands:
        return False, f"command_not_allowed_{cmd}"

    return True, "command_allowed"
