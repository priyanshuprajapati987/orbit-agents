# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Assistant — re-export from canonical inner package.

The canonical implementation lives in orbit/orbit/assistant.py (1483+ lines).
This file re-exports it so that `from orbit.assistant import Assistant` works.
"""

from .orbit.assistant import Assistant, TOOL_DEFINITIONS  # noqa: F401

__all__ = ["Assistant", "TOOL_DEFINITIONS"]
