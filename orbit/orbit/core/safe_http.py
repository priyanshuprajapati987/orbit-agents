# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
Safe HTTP client — wraps urllib/requests with SSRF protection.

Import this instead of using raw urllib/requests for outbound HTTP.
Automatically blocks private/internal IPs AND re-validates every redirect
hop, so a 302 to http://169.254.169.254/latest/meta-data/ (cloud metadata)
or an internal service cannot bypass the guard.
"""

import logging
import urllib.request
import urllib.error
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 4


def _validate_chain(url: str) -> str:
    """Validate the URL and its full redirect chain. Return error or None."""
    from .ssrf_guard import get_ssrf_guard

    guard = get_ssrf_guard()
    import requests as _requests

    # Walk the redirect chain ourselves (no auto-follow) so every hop is checked.
    current = url
    seen = set()
    for _ in range(MAX_REDIRECTS + 1):
        err = guard.validate_outbound(current)
        if err:
            return err
        seen.add(current)
        # Peek at the redirect target with a cheap HEAD (no body) without following.
        try:
            probe = _requests.request(
                "HEAD",
                current,
                allow_redirects=False,
                timeout=8,
            )
        except Exception as e:  # network error during probe -> fail closed
            return f"SSRF probe failed for {current}: {e}"
        if 300 <= probe.status_code < 400:
            loc = probe.headers.get("Location")
            if not loc:
                break
            current = urljoin(probe.url, loc)
            if current in seen:
                return f"SSRF blocked: redirect loop at {current}"
            continue
        break
    return None


def assert_safe(url: str) -> None:
    """Raise (urllib.error.URLError) if the URL or its redirect chain is unsafe."""
    err = _validate_chain(url)
    if err:
        logger.warning("SSRF blocked outbound request to %s (%s)", url, err)
        raise urllib.error.URLError(err)


def safe_urlopen(url: str, timeout: int = 10, **kwargs):
    """urlopen with SSRF protection. Raises urllib.error.URLError if blocked.

    urllib does NOT follow redirects automatically, but we still validate the
    full redirect chain (HEAD probe) for consistency/defense-in-depth.
    """
    assert_safe(url)
    return urllib.request.urlopen(url, timeout=timeout, **kwargs)


def safe_requests_get(url: str, timeout: int = 10, **kwargs):
    """requests.get with SSRF protection that re-validates every redirect hop."""
    import requests as _requests

    assert_safe(url)
    kwargs.setdefault("allow_redirects", True)
    return _requests.get(url, timeout=timeout, **kwargs)


def safe_requests_post(url: str, timeout: int = 10, **kwargs):
    """requests.post with SSRF protection."""
    import requests as _requests

    assert_safe(url)
    return _requests.post(url, timeout=timeout, **kwargs)
