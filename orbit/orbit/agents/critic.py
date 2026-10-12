# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import json

from ..core.llm import legacy_adapter as llm_router
from .base import BaseAgent
from ..logging_config import get_logger

logger = get_logger(__name__)


CRITIC_PROMPT = """You are the CRITIC AGENT. Review execution results and decide if they are correct.
Output ONLY valid JSON:
{"quality": "pass|fail|retry", "score": 0.0-1.0, "feedback": "...", "suggested_fix": "..."}

Rules:
- pass (score>0.7): result looks correct
- fail (score<0.4): result is wrong or empty
- retry (0.4-0.7): result is partial, suggest fix
- Be strict: empty results, errors, or off-topic = fail
- For code: check if it compiles, has no placeholders
- For research: check if it has actual content, not just headers"""


class CriticAgent(BaseAgent):
    label = "CRT"
    name = "Critic"
    color = "#ef4444"
    description = "Reviews output quality, requests retry if wrong"

    MAX_RETRIES = 2

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._file_tool = None
        self._computer_tool = None
        self._search_tool = None

    def _get_file_tool(self):
        if self._file_tool is None:
            from ..tools.file_tools import FileTools

            self._file_tool = FileTools()
        return self._file_tool

    def _get_computer_tool(self):
        if self._computer_tool is None:
            from ..tools.computer import ComputerTool

            self._computer_tool = ComputerTool()
        return self._computer_tool

    def _get_search_tool(self):
        if self._search_tool is None:
            from ..tools.web_search import WebSearchTool

            self._search_tool = WebSearchTool()
        return self._search_tool

    def can_handle(self, text, intent=None, confidence=0.0):
        return True

    def review(self, subtask: dict, result: str, attempt: int = 0) -> dict:
        if not isinstance(subtask, dict) or not result or not str(result).strip():
            return {"quality": "fail", "score": 0.0, "feedback": "Empty result", "suggested_fix": "Retry"}
        if str(result).strip().startswith("[Error"):
            return {"quality": "fail", "score": 0.0, "feedback": "Error result", "suggested_fix": "Retry"}
        tool_evidence = ""
        stype = subtask.get("type", "general")

        if stype in ("code", "shell"):
            try:
                import os as _os
                import py_compile
                import tempfile

                snippet = str(result)[:500].strip()
                with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8") as tmp:
                    tmp.write(snippet)
                    tmp_path = tmp.name
                try:
                    py_compile.compile(tmp_path, doraise=True)
                    tool_evidence = "\n[Compile check: PASSED - python syntax valid]"
                except Exception as e:
                    tool_evidence = f"\n[Compile check: FAILED - {str(e)[:200]}]"
                finally:
                    try:
                        _os.unlink(tmp_path)
                    except Exception:
                        pass
            except Exception:
                logger.exception("Critic compile check failed")

        elif stype == "research":
            try:
                search = self._get_search_tool()
                if search:
                    r = search.search(result[:100])
                    results = r.get("results", []) if isinstance(r, dict) else (r or [])
                    if results:
                        tool_evidence = f"\n[Web verification: {len(results)} results found]"
                    else:
                        tool_evidence = "\n[Web verification: no results - possible hallucination]"
            except Exception:
                logger.exception("Critic web verification search failed")

        prompt = f"""{CRITIC_PROMPT}

Subtask: {json.dumps(subtask)}
Type: {stype}
Attempt: {attempt + 1}/{self.MAX_RETRIES}{tool_evidence}
Result to review:
{result[:1000]}

Output JSON verdict:"""
        try:
            response = llm_router.generate(prompt, force_offline=True)
            if not response:
                return {"quality": "retry", "score": 0.5, "feedback": "No response from critic", "suggested_fix": ""}
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()
            verdict = json.loads(response)
            if isinstance(verdict, dict) and "quality" in verdict:
                return verdict
        except Exception:
            logger.exception("Critic failed to parse LLM JSON verdict")
            return {
                "quality": "retry",
                "score": 0.5,
                "feedback": "Critic LLM returned invalid JSON",
                "suggested_fix": "Retry",
            }
        if not result or result.startswith("[Error") or result.startswith("[Need input"):
            return {
                "quality": "fail",
                "score": 0.0,
                "feedback": "Empty or error result",
                "suggested_fix": "Retry with different approach",
            }
        return {
            "quality": "retry",
            "score": 0.5,
            "feedback": "Critic LLM did not return a valid verdict",
            "suggested_fix": "Retry review",
        }

    def needs_retry(self, verdict: dict, attempt: int) -> bool:
        quality = verdict.get("quality", "fail")
        if quality == "pass":
            return False
        if quality == "fail" and attempt >= self.MAX_RETRIES - 1:
            return False
        return attempt < self.MAX_RETRIES - 1

    def _run(self, text, context=None):
        import re

        text_lower = text.lower()

        if "save report" in text_lower or "export review" in text_lower or "write review" in text_lower:
            save_match = re.search(
                r"(?:save|export|write)\s+(?:report|review)?\s*(?:to\s+)?[`\"']?([^\s`\"']+\.\w+)[`\"']?", text_lower
            )
            result = context or text
            verdict = self.review({"type": "general", "description": text}, result)
            report = json.dumps(verdict, indent=2)
            if save_match:
                filepath = save_match.group(1)
                ft = self._get_file_tool()
                if ft:
                    try:
                        write_result = ft.write(filepath, report)
                        if write_result and write_result.get("success"):
                            return f"Review saved to {filepath}:\n```json\n{report}\n```"
                    except Exception:
                        logger.exception("Critic failed to save review to file")
            return f"```json\n{report}\n```"

        result = context or text
        return json.dumps(self.review({"type": "general", "description": text}, result))
