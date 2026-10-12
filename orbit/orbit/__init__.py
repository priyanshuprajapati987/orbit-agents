"""
ORBIT Multi-Agent Framework - public distribution.

An event-driven multi-agent system: a Queen orchestrator coordinates a
Council of advisors, a Planner, an Executor, a Critic and 25 field
specialist agents (research, code, writing, SEO, media, security, memory...)
over a shared tool gateway and memory layer.

Package layout:
    orbit/            # top-level package (public API + thin re-exports)
        orbit/        # inner package (implementations)
            agents/   # Queen, Council, Planner, Executor, 25 specialists
            core/     # orchestration support (routing, memory, policy...)
            tools/    # tool integrations behind the tool gateway

Author: Priyanshu Prajapati
(c) 2026 Priyanshu Prajapati. All rights reserved per LICENSE (Elastic License 2.0).
"""

__version__ = "3.7.0"
__codename__ = "agents"
__orbit_public_id__ = "OBP-2026-4a77efcb85bd43f3"

try:
    from dotenv import load_dotenv
    from .core.runtime_paths import env_path as _runtime_env_path

    env_path = _runtime_env_path()
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass
