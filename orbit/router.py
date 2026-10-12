# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Router — Thin re-export from inner package.

The canonical implementation lives in orbit/orbit/router.py.
This module re-exports the public API for external consumers.
"""

from .orbit.router import (
    check_internet,
    generate,
    generate_offline,
    get_status,
    get_tier_list,
    list_ollama_models,
    pull_ollama_model,
    set_tier,
)

__all__ = [
    "check_internet",
    "generate",
    "generate_offline",
    "get_status",
    "get_tier_list",
    "list_ollama_models",
    "pull_ollama_model",
    "set_tier",
]
