# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""Shared environment-variable numeric parsing helpers.

A bad value (e.g. ``MIN_FEEDBACK=abc``) in an unguarded ``int(os.getenv(...))``
crashes import/boot. These helpers return the default instead, with optional
clamping for values where out-of-range numbers cause hangs/loops
(timeouts > 0, intervals >= 1).

Stdlib only — no local imports (avoids import cycles; safe to use from
``core/config.py``, ``orbit/config.py`` and every core module).
"""

import os


def int_env(name: str, default: int, lo=None, hi=None) -> int:
    """Read ``name`` as int; return ``default`` on missing/empty/bad value.

    ``lo``/``hi`` clamp the result (only pass them where an out-of-range
    value causes hangs/loops, e.g. timeouts and intervals).
    """
    raw = os.getenv(name)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError, AttributeError):
            return default
    if lo is not None and value < lo:
        value = lo
    if hi is not None and value > hi:
        value = hi
    return value


def float_env(name: str, default: float, lo=None, hi=None) -> float:
    """Read ``name`` as float; return ``default`` on missing/empty/bad value.

    ``lo``/``hi`` clamp the result (only pass them where an out-of-range
    value causes hangs/loops, e.g. timeouts and intervals).
    """
    raw = os.getenv(name)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        value = default
    else:
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError, AttributeError):
            return default
    if lo is not None and value < lo:
        value = lo
    if hi is not None and value > hi:
        value = hi
    return value
