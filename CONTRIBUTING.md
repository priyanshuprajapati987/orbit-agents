# Contributing to ORBIT Multi-Agent Framework

Thanks for helping make ORBIT better! This guide shows exactly how to add a
new specialist agent **without touching core code**.

## Development setup

```bash
git clone https://github.com/priyanshuprajapati987/orbit-agents
cd orbit-agents
python -m venv .venv
.venv\Scripts\activate       # Windows
source .venv/bin/activate    # macOS/Linux
pip install -e .
```

Configure any OpenAI-compatible LLM provider (or a local Ollama server) via
environment variables, then verify the example runs:

```bash
python examples/hello_orbit.py
```

## Adding a specialist agent (5 steps)

1. **Create the agent** — new file `orbit/orbit/agents/my_agent.py`:

```python
from .base import BaseAgent

class MySpecialistAgent(BaseAgent):
    """One-line description of what this specialist does."""

    def execute(self, text, context=None):
        # Your specialist logic here. Return a string result.
        return f"handled: {text}"
```

2. **Register it** — add it to the exports in
   `orbit/orbit/agents/__init__.py` and to `AGENT_REGISTRY` with a short
   uppercase key (existing pattern: `RES`, `COD`, `SEO`, ...).

3. **Test it locally**:

```python
from orbit.agents import MySpecialistAgent
print(MySpecialistAgent().execute("hello"))
```

4. **Document it** — add a row for your agent in the README agents table.

5. **Open a PR** — describe what the specialist does, which registry key it
   uses, and paste a short sample output.

## Code conventions

- Python 3.10+, type hints on public methods
- Every module starts with the license/trace header (keep it intact)
- Keep agents **decoupled**: no imports from `server`/blueprints/product UI
- Errors: return human-readable strings from `execute()`, don't raise for
  expected failure modes (missing key, network down)
- Lint: `ruff check orbit/` (config in upstream repo; `ruff` optional)

## What stays out of scope

This repo is the **agent framework only**. The private ORBIT product layer
(billing, auth, key vault, desktop GUI, blueprints) is intentionally excluded
— see `orbit/orbit/core/provider_keys.py` for the stub pattern used to keep
import surfaces stable.

## Reporting issues

Open a GitHub issue with: OS, Python version, provider (or `ollama`), the
agent involved, and the minimal snippet that reproduces the problem.

---

(c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
