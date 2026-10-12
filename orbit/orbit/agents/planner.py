# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import json

from ..core.llm import legacy_adapter as llm_router
from ..config import ORBIT_SYSTEM_PROMPT
from .base import BaseAgent
from ..logging_config import get_logger

logger = get_logger(__name__)


PLANNER_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the PLANNER AGENT. Break user tasks into subtasks.
Output ONLY valid JSON:
{{"subtasks": [{{"id": 1, "type": "research|code|file|memory|shell|ask", "description": "...", "dependencies": []}}], "parallel": false}}

Rules:
- research: web/news search
- code: write/analyze code
- file: file operations
- memory: save/recall context
- shell: system commands
- ask: ask user for clarification
- Keep subtasks focused and minimal
- Use dependencies to order subtasks
- Max 5 subtasks"""


class PlannerAgent(BaseAgent):
    label = "PLN"
    name = "Planner"
    color = "#f59e0b"
    description = "Breaks tasks into subtask plans"

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._file_tool = None

    def _get_file_tool(self):
        if self._file_tool is None:
            from ..tools.file_tools import FileTools

            self._file_tool = FileTools()
        return self._file_tool

    def can_handle(self, text, intent=None, confidence=0.0):
        return True

    def _run(self, text, context=None):
        import re

        text_lower = text.lower()

        if "save plan" in text_lower or "export plan" in text_lower or "write plan" in text_lower:
            save_match = re.search(
                r"(?:save|export|write)\s+(?:plan\s+)?(?:to\s+)?[`\"']?([^\s`\"']+\.\w+)[`\"']?", text_lower
            )
            safe = text.replace("{", "{{").replace("}", "}}")
            prompt = f"""{PLANNER_PROMPT}\n\nUser task: {safe}\n\nOutput the plan as JSON only:"""
            try:
                response = llm_router.generate(prompt, force_offline=False)
                if response:
                    if "```json" in response:
                        response = response.split("```json")[1].split("```")[0].strip()
                    elif "```" in response:
                        response = response.split("```")[1].split("```")[0].strip()
                    plan = json.loads(response)
                    plan_str = json.dumps(plan, indent=2)
                    if save_match:
                        filepath = save_match.group(1)
                        ft = self._get_file_tool()
                        if ft:
                            try:
                                result = ft.write(filepath, plan_str)
                                if result and result.get("success"):
                                    plan["_saved_to"] = filepath
                            except Exception:
                                logger.exception("Planner failed to save plan to file")
                    return plan
            except Exception:
                logger.exception("Planner LLM generation failed")

        safe = text.replace("{", "{{").replace("}", "}}")
        prompt = f"""{PLANNER_PROMPT}

User task: {safe}

Output the plan as JSON only:"""
        try:
            response = llm_router.generate(prompt, force_offline=False)
            if not response:
                return {
                    "subtasks": [{"id": 1, "type": "ask", "description": "Could not parse task"}],
                    "parallel": False,
                }
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()
            plan = json.loads(response)
            if isinstance(plan, dict) and "subtasks" in plan:
                return plan
            return {"subtasks": [{"id": 1, "type": "ask", "description": "Invalid plan format"}], "parallel": False}
        except Exception:
            return {"subtasks": [{"id": 1, "type": "ask", "description": "Could not parse task"}], "parallel": False}
