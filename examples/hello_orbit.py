"""ORBIT hello-world: Queen -> Planner -> Executor -> specialist.

Runs end-to-end with any OpenAI-compatible provider key in the
environment (or a local Ollama server). See README Quickstart.

(c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0 | ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3
"""

from orbit.agents import QueenAgent, CouncilAgent, PlannerAgent, ExecutorAgent


def main():
    task = "Research the top 3 trends in multi-agent AI and summarize each in one sentence."

    print("== Queen receives task ==")
    queen = QueenAgent()

    print("== Planner decomposes ==")
    planner = PlannerAgent()
    plan = planner.run(task) if hasattr(planner, "run") else planner.plan(task)
    print(plan)

    print("== Council reviews (advisors) ==")
    council = CouncilAgent()
    review = council.run(str(plan)) if hasattr(council, "run") else None
    print(review)

    print("== Executor runs subtasks ==")
    executor = ExecutorAgent()
    result = executor.run(str(plan)) if hasattr(executor, "run") else None
    print(result)

    print("== Queen end-to-end (single call) ==")
    print(queen.run(task))


if __name__ == "__main__":
    main()
