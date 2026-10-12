# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT - Just A Rather Very Efficient System
Version: 3.7.0 | Codename: "Demons Army" | Release: "Phase A+B+C - boot wiring + unified router + desktop packaging + intelligence/shock"

A PyQt5 desktop AI assistant with voice I/O, multi-provider LLM routing,
25 agents, 24 tools, 496 core modules, and 132 React components.

Package Structure:
    orbit/              # Top-level package (public API + thin re-exports)
        orbit/          # Inner package (actual implementations)
            agents/      # 25 Agent implementations
            tools/       # 24 Tool implementations
            core/        # 87+ Core systems
            blueprints/  # Flask API blueprints
            gui.py       # PyQt5 desktop GUI
            assistant.py # Main assistant logic
            config.py    # Configuration
            router.py    # Legacy LLM routing (fallback only)
            core/llm/    # Canonical router + sync facade
            ...

    The top-level orbit/ re-exports key classes from orbit/orbit/
    for convenience. Always import from orbit.* (top-level) in user code.

Canonical env file: repo-root .env (loaded by all packages).
Main Entry Point: main.py
Author: Priyanshu Prajapati
"""

__version__ = "3.7.0"
__codename__ = "Demons Army"

try:
    from dotenv import load_dotenv
    from pathlib import Path

    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

try:
    from . import orbit  # noqa: F401
except Exception:
    pass
