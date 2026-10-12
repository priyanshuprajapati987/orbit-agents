"""Runtime provenance fingerprint for the ORBIT Multi-Agent Framework.

Lets anyone verify that a running installation originates from this
distribution - and lets the author detect copies that surface in the wild
(compare their reported id against the registry below).

This is a transparency mechanism, not DRM: it carries no user data, phones
home nowhere, and can be read by anyone.

ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati
SPDX-License-Identifier: Elastic-2.0
"""

from __future__ import annotations

__all__ = ["fingerprint", "verify"]

FINGERPRINT = "OBP-2026-4a77efcb85bd43f3"

# Known-good ids for this distribution (extend on forks/releases).
_ORIGINALS = frozenset({FINGERPRINT})


def fingerprint() -> str:
    """Return the public distribution id of this installation."""
    return FINGERPRINT


def verify() -> bool:
    """Return True when this build carries an original distribution id."""
    return FINGERPRINT in _ORIGINALS
