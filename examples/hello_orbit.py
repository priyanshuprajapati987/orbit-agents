"""ORBIT hello-world: Queen -> Planner -> Executor -> specialist.

Every agent exposes the same interface: ``execute(task) -> str``.

Requires an LLM provider configured via environment, e.g.:

    export OPENAI_API_KEY=...        # any OpenAI-compatible provider
    # or a local model:
    export OLLAMA_URL=http://localhost:11434

Run with:  python examples/hello_orbit.py

(c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0 | ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3
"""

from orbit.agents import (
    AGENT_REGISTRY,
    CriticAgent,
    ExecutorAgent,
    PlannerAgent,
    QueenAgent,
)

TASK = "Research the top 3 trends in multi-agent AI and summarize each in one sentence."


def main():
    # Windows consoles default to cp1252; agents may return emoji/unicode.
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("== 1. Planner decomposes the task ==")
    plan = PlannerAgent().execute(TASK)
    print(plan)

    print("== 2. Critic reviews the plan ==")
    critic = CriticAgent()
    review = critic.execute(str(plan))
    print(review)

    print("== 3. Executor runs the plan ==")
    outcome = ExecutorAgent().execute(str(plan))
    print(outcome)

    print("== 4. Queen end-to-end (wired to the full registry) ==")
    queen = QueenAgent()
    crew = {}
    for key, value in AGENT_REGISTRY.items():
        if key == "QUEEN":
            continue
        try:
            crew[key] = value() if isinstance(value, type) else value
        except Exception:
            continue
    queen.set_agents(crew)
    final = queen.execute(TASK)
    print(final if final is not None else "(Queen delegated to the crew; see agent logs above)")


if __name__ == "__main__":
    main()
