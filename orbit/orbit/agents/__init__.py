# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Multi-Agent System

This package contains the multi-agent architecture for ORBIT:

Core Agents:
- QueenAgent: Central orchestrator that plans and delegates tasks
- CouncilAgent: 5-advisor council for complex decisions
- PlannerAgent: Task decomposition into subtasks
- ExecutorAgent: Subtask execution
- CriticAgent: Quality review of agent outputs

Specialized Agents (in submodules):
- TaskAgent, CodeAgent — task_agents.py
- ResearchAgent, WritingAgent, SEOAgent, FineTuneAgent, ThumbnailAgent — research_agents.py
- ScreenAgent, SocialAgent, MeetingAgent — media_agents.py
- QuarantineAgent, LogAnalystAgent, CoordinationAgent, DecisionAgent, SecurityAgent,
  MemoryAgent, HermesAgent, MultiModalAgent — utility_agents.py, multimodal_agent.py

Pipeline:
- PipelineOrchestrator: Plan→Execute→Review→Retry→Synthesize loop
- PipelineMemoryAgent: Stores pipeline results

Usage:
    from orbit.agents import QueenAgent, AGENT_REGISTRY

    queen = QueenAgent()
    result = queen.run("Research the latest AI news")
"""

from .council import CouncilAgent
from .critic import CriticAgent
from .executor_agent import ExecutorAgent
from .memory_agent import PipelineMemoryAgent
from .orchestrator import PipelineOrchestrator  # noqa: F401
from .planner import PlannerAgent
from .queen import QueenAgent
from .task_agents import TaskAgent, CodeAgent
from .research_agents import (
    ResearchAgent,
    WritingAgent,
    SEOAgent,
    FineTuneAgent,
    ThumbnailAgent,
)
from .media_agents import ScreenAgent, SocialAgent, MeetingAgent
from .multimodal_agent import MultiModalAgent
from .utility_agents import (
    QuarantineAgent,
    LogAnalystAgent,
    CoordinationAgent,
    DecisionAgent,
    SecurityAgent,
    SecurityGuardAgent,
    MemoryAgent,
    HermesAgent,
)

AGENT_REGISTRY = {
    "QUEEN": QueenAgent,
    "TAS": TaskAgent,
    "COD": CodeAgent,
    "RES": ResearchAgent,
    "WRI": WritingAgent,
    "COA": CoordinationAgent,
    "DEC": DecisionAgent,
    "SEC": SecurityAgent,
    "SECG": SecurityGuardAgent,
    "MEM": MemoryAgent,
    "SCR": ScreenAgent,
    "SOC": SocialAgent,
    "MTG": MeetingAgent,
    "FTN": FineTuneAgent,
    "THB": ThumbnailAgent,
    "SEO": SEOAgent,
    "QNT": QuarantineAgent,
    "LOG": LogAnalystAgent,
    "CNC": CouncilAgent,
    "PLN": PlannerAgent,
    "EXE": ExecutorAgent,
    "PMA": PipelineMemoryAgent,
    "CRT": CriticAgent,
    "HRM": HermesAgent,
    "MMA": MultiModalAgent,
}
