# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import threading
import time

from ..core.llm import legacy_adapter as llm_router
from .base import BaseAgent
from ..logging_config import get_logger

logger = get_logger(__name__)


ADVISOR_PROMPTS = {
    "contrarian": """You are Advisor 1: THE CONTRARIAN.
Your job is to find flaws in EVERY plan.
- Identify risks, weak assumptions, failure scenarios
- Question everything — do not accept anything at face value
- Be harsh, critical, and pessimistic
- Find hidden dangers others missed
- Do NOT be optimistic or supportive""",
    "first_principles": """You are Advisor 2: FIRST PRINCIPLES THINKER.
Your job is to break problems down to fundamentals.
- Ignore industry norms, conventional wisdom, existing assumptions
- Ask: What are the immutable truths here?
- Rebuild the solution from scratch using only first principles
- Challenge why things are done the way they are
- Think from physics-level reality upward""",
    "expansionist": """You are Advisor 3: THE EXPANSIONIST.
Your job is to find OPPORTUNITIES everywhere.
- Focus on bigger outcomes, hidden leverage
- Identify new business models, growth angles
- Think 10x bigger than everyone else
- Find ways to turn constraints into advantages
- Be optimistic, ambitious, and visionary""",
    "outsider": """You are Advisor 4: THE OUTSIDER.
You know NOTHING about this industry or domain.
- Look at the problem with completely fresh eyes
- Challenge things insiders take for granted
- Ask naive questions that reveal hidden assumptions
- Suggest unconventional, weird, creative solutions
- Your ignorance is your superpower""",
    "executor": """You are Advisor 5: THE EXECUTOR.
You ONLY care about implementation.
- Focus on: what to do next, priorities, resources, timelines
- Convert every idea into concrete action steps
- Identify dependencies, blockers, and critical paths
- Be practical, realistic, and specific
- Ignore theory — only what can actually be done""",
}

CHAIRMAN_PROMPT = """You are THE CHAIRMAN of the advisory council.
You have heard from ALL 5 advisors. Now produce the final recommendation.

Review every perspective:
1. Contrarian — found flaws and risks
2. First Principles Thinker — rebuilt from fundamentals
3. Expansionist — found opportunities
4. Outsider — challenged assumptions with fresh eyes
5. Executor — created action plans

Identify:
- AGREEMENTS — where do multiple advisors align?
- DISAGREEMENTS — where do they conflict?
- RESOLVE conflicts with reasoning

Produce final answer with these sections:
- Key Insights
- Top 3 Risks
- Top 3 Opportunities
- Next 7 Days Action Plan
- Final Verdict (bold recommendation)"""


class CouncilAgent(BaseAgent):
    label = "CNC"
    name = "Council"
    color = "#ff6b6b"
    description = "5-advisor council + chairman for deep analysis"

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._advisor_cache = {}
        self._on_advisor_status = None
        self._file_tool = None
        self._search_tool = None

    def _get_file_tool(self):
        if self._file_tool is None:
            from ..tools.file_tools import FileTools

            self._file_tool = FileTools()
        return self._file_tool

    def _get_search_tool(self):
        if self._search_tool is None:
            from ..tools.web_search import WebSearchTool

            self._search_tool = WebSearchTool()
        return self._search_tool

    def set_callback(self, on_advisor_status=None):
        self._on_advisor_status = on_advisor_status

    def can_handle(self, text, intent=None, confidence=0.0):
        text_lower = text.lower()
        if text_lower.startswith("council:") or text_lower.startswith("council "):
            return True
        if intent == "council":
            return True
        return False

    def _emit_advisor(self, name):
        if self._on_advisor_status:
            self._on_advisor_status(name)

    def _run_advisor(self, advisor_name, user_text):
        prompt = ADVISOR_PROMPTS.get(advisor_name, "")
        search_evidence = ""
        try:
            search = self._get_search_tool()
            if search:
                r = search.search(user_text[:200])
                results = r.get("results", []) if isinstance(r, dict) else (r or [])
                if results:
                    snippets = [item.get("snippet", "") for item in results[:3] if item.get("snippet")]
                    if snippets:
                        search_evidence = "\n\n[Web Research Evidence]\n" + "\n".join(f"- {s}" for s in snippets)
        except Exception:
            logger.exception("Council advisor web search failed")
        full_prompt = f"{prompt}{search_evidence}\n\nAnalyze this:\n{user_text}"
        try:
            result = llm_router.generate(full_prompt, force_offline=False)
            return result or f"[{advisor_name}: no response]"
        except Exception as e:
            return f"[{advisor_name} error: {e}]"

    def _run_chairman(self, user_text, advisor_results):
        parts = []
        for name, result in advisor_results.items():
            parts.append(f"--- {name.upper()} ---\n{result}")

        full_prompt = (
            f"{CHAIRMAN_PROMPT}\n\nOriginal question:\n{user_text}\n\nAdvisor responses:\n\n{chr(10).join(parts)}"
        )
        try:
            result = llm_router.generate(full_prompt, force_offline=False)
            return result or "[Chairman: no response]"
        except Exception as e:
            return f"[Chairman error: {e}]"

    def _run(self, text, context=None):
        import re

        user_text = text

        council_triggers = ["council:", "council ", "advisors:", "advisory:"]
        text_lower = text.lower()
        for t in council_triggers:
            if t in text_lower:
                idx = text_lower.find(t) + len(t)
                user_text = text[idx:].strip()
                break

        save_match = re.search(
            r"(?:save|export|write)\s+(?:report\s+)?(?:to\s+)?[`\"']?([^\s`\"']+\.\w+)[`\"']?", text_lower
        )
        if save_match:
            user_text = re.sub(
                r"(?:save|export|write)\s+(?:report\s+)?(?:to\s+)?[`\"']?[^\s`\"']+\.\w+[`\"']?", "", user_text
            ).strip()
            if not user_text:
                user_text = text

        advisors = ["contrarian", "first_principles", "expansionist", "outsider", "executor"]

        results = {}
        threads = []
        lock = threading.Lock()

        def run_one(name):
            self._emit_advisor(name)
            result = self._run_advisor(name, user_text)
            with lock:
                results[name] = result

        for name in advisors:
            t = threading.Thread(target=run_one, args=(name,), daemon=True)
            t.start()
            threads.append(t)
            time.sleep(0.3)

        for t in threads:
            t.join()

        self._emit_advisor("chairman")
        final = self._run_chairman(user_text, results)

        header = "👑 **ADVISORY COUNCIL ANALYSIS**\n\n"
        advisor_icons = {
            "contrarian": "1️⃣ **Contrarian**",
            "first_principles": "2️⃣ **First Principles**",
            "expansionist": "3️⃣ **Expansionist**",
            "outsider": "4️⃣ **Outsider**",
            "executor": "5️⃣ **Executor**",
        }

        advisor_summaries = []
        for name in advisors:
            r = results.get(name, "")
            icon = advisor_icons.get(name, name)
            summary = r[:200] + "..." if len(r) > 200 else r
            advisor_summaries.append(f"{icon}\n{summary}")

        full = header + "\n\n".join(advisor_summaries) + "\n\n---\n\n**🏛️ CHAIRMAN'S FINAL VERDICT**\n\n" + final

        if save_match:
            filepath = save_match.group(1)
            ft = self._get_file_tool()
            if ft:
                try:
                    result = ft.write(filepath, full)
                    if result and result.get("success"):
                        return f"{full}\n\n---\nSaved to: {filepath}"
                except Exception:
                    logger.exception("Council failed to save report to file")

        return full
