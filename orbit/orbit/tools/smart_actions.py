# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Smart Actions — Quick Fix, Code Actions, Error Resolution
"""

from ..logging_config import get_logger
from ..core.llm import legacy_adapter as llm_router

logger = get_logger(__name__)


class SmartActions:
    """Quick Fixes + Code Actions using LLM or built-in patterns."""

    AVAILABLE_ACTIONS = {
        "explain": "Explain the selected code",
        "fix": "Fix errors in the selected code",
        "optimize": "Optimize the selected code",
        "document": "Add docstrings/comments to the selected code",
        "format": "Format the selected code",
        "find_bugs": "Find potential bugs in the selected code",
        "add_tests": "Generate unit tests for the selected code",
        "refactor": "Suggest refactoring for the selected code",
    }

    def quick_fix(self, error_text: str, code_context: str = "", language: str = "python") -> dict:
        """Generate a quick fix for an error."""
        prompt = f"""Fix this {language} error:
Error: {error_text}

Code:
{code_context}

Return ONLY the fixed code, no explanation."""

        try:
            result = llm_router.generate(prompt, system_prompt="You are a senior developer fixing bugs.")
            return {"success": True, "fix": result, "error": error_text}
        except Exception as e:
            logger.error(f"Quick fix failed: {e}")
            return {"success": False, "fix": None, "error": error_text, "message": str(e)}

    def run_action(self, action: str, code: str, language: str = "python") -> dict:
        """Run a named smart action on a code block."""
        action = action.lower()
        if action not in self.AVAILABLE_ACTIONS:
            return {
                "success": False,
                "error": f"Unknown action: {action}",
                "available": list(self.AVAILABLE_ACTIONS.keys()),
            }

        prompts = {
            "explain": f"Explain this {language} code briefly:\n\n{code}",
            "fix": f"Fix any errors in this {language} code, return only the fixed code:\n\n{code}",
            "optimize": f"Optimize this {language} code for performance, return only the optimized code:\n\n{code}",
            "document": f"Add docstrings and comments to this {language} code, return the documented code:\n\n{code}",
            "format": f"Format this {language} code properly, return only the formatted code:\n\n{code}",
            "find_bugs": f"Find potential bugs in this {language} code and explain them:\n\n{code}",
            "add_tests": f"Generate unit tests for this {language} code:\n\n{code}",
            "refactor": f"Suggest refactoring for this {language} code, return the improved code:\n\n{code}",
        }

        prompt = prompts.get(action, f"Perform {action} on this code:\n\n{code}")

        try:
            result = llm_router.generate(prompt, system_prompt="You are a senior developer assistant.")
            return {"success": True, "action": action, "result": result}
        except Exception as e:
            logger.error(f"Action '{action}' failed: {e}")
            return {"success": False, "action": action, "error": str(e)}

    def list_actions(self) -> dict:
        return {"actions": self.AVAILABLE_ACTIONS}
