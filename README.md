# ORBIT Multi-Agent Framework‌​‌‌​‌​‌‌​​​‌​​​​​​‌​​​​​​‌‌​‌​​​‌‌‌‌​‌​​‌​​​​‌​‌​‌‌‌‌​​​​​​‌‌​​

[![License](https://img.shields.io/badge/license-Elastic--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)

A Python multi-agent framework where a **Queen orchestrator** coordinates a
**Council of advisors**, a **Planner**, an **Executor**, a **Critic**, and
**25 field-specialist agents** over a shared tool gateway and memory layer.

> Framework extracted from the private **ORBIT** assistant project
> (https://comfy-dasik-e7cdcb.netlify.app). This repository contains the
> agent orchestration system only.

## Architecture

```
User task
   |
   v
QueenAgent  ------->  CouncilAgent (5 advisors, complex decisions)
   |
   +--> PlannerAgent  (task -> subtasks)
   +--> ExecutorAgent (subtask -> tool calls via ToolGate)
   +--> CriticAgent   (quality review, retry loop)
   |
   v
25 specialist agents (research, code, writing, SEO, media, security...)
   |
   v
memory layer + tool gateway (browser, calendar, files, github, web...)
```

## The Agents

| Group | Agents |
|---|---|
| Orchestration | QueenAgent (hub), CouncilAgent (5 advisors), PlannerAgent, ExecutorAgent, CriticAgent |
| Pipeline | PipelineOrchestrator (Plan-Execute-Review-Retry-Synthesize), PipelineMemoryAgent |
| Task / Code | TaskAgent, CodeAgent |
| Research / Content | ResearchAgent, WritingAgent, SEOAgent, FineTuneAgent, ThumbnailAgent, DailyBriefingAgent |
| Media / Social | ScreenAgent, SocialAgent, MeetingAgent |
| Utility / Security | QuarantineAgent, LogAnalystAgent, CoordinationAgent, DecisionAgent, SecurityAgent, SecurityGuardAgent, MemoryAgent, HermesAgent, MultiModalAgent |

## Quickstart

```bash
pip install -e .
```

```python
from orbit.agents import QueenAgent

queen = QueenAgent()
result = queen.run("Research the latest AI news and summarize in 3 bullets")
print(result)
```

Agents route through the shared LLM router. Configure any OpenAI-compatible
provider via environment variables (see `orbit/orbit/config.py`), e.g.:

```bash
export OPENAI_API_KEY=...        # or your provider's key
# or run a local model:
export OLLAMA_URL=http://localhost:11434
```

Full runnable demo: [`examples/hello_orbit.py`](examples/hello_orbit.py)
(Queen -> Planner -> Executor -> specialist, end to end).

## Repository layout

```
orbit/
  __init__.py          # top-level re-exports (public API)
  orbit/
    agents/            # 30 agent classes - the framework core
    core/              # routing, memory, policy, safety support modules
    tools/             # tool integrations
    config.py          # configuration hub
    assistant.py       # assistant glue (TOOL_DEFINITIONS etc.)
```

## Contributing

Want to add a specialist agent? Create `orbit/orbit/agents/my_agent.py`
subclassing `BaseAgent`, register it in `AGENT_REGISTRY`, add it to the
docs table above, and open a PR. See existing agents for the pattern.

## License

[Elastic License 2.0 (ELv2)](LICENSE) - free to use, modify and
redistribute with attribution; you may **not** provide the software to
third parties as a hosted or managed service, and derivative works must
remain under ELv2.

Dual licensing available for commercial integration: contact the author
via GitHub.

## Author

**Priyanshu Prajapati** - [github.com/priyanshuprajapati987](https://github.com/priyanshuprajapati987)

(c) 2026 Priyanshu Prajapati. ORBIT-TRACE: `OBP-2026-4a77efcb85bd43f3`
