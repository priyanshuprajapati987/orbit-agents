# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import json
import logging
import re
import threading

logger = logging.getLogger(__name__)

# Matches the actual agent error formats: "[TAS error: ...]",
# "[Executor error in code: ...]", "[contrarian error: ...]",
# "[BrowserTool not available]", "[Error: ...]" (legacy adapter).
_AGENT_ERROR_RE = re.compile(r"\[\s*\w[\w\s]*\b(?:error|not available)\b", re.IGNORECASE)


def _is_agent_error(result) -> bool:
    """True when an agent result string is an error report, not an answer."""
    if not isinstance(result, str) or not result:
        return False
    head = result[:60]
    return bool(_AGENT_ERROR_RE.search(head))

from ..config import ORBIT_SYSTEM_PROMPT, OLLAMA_URL, DEFAULT_OLLAMA_MODEL
from .base import BaseAgent

ALL_AGENTS = {
    "TAS": "TaskAgent",
    "COD": "CodeAgent",
    "RES": "ResearchAgent",
    "WRI": "WritingAgent",
    "COA": "CoordinationAgent",
    "DEC": "DecisionAgent",
    "SEC": "SecurityAgent",
    "SECG": "SecurityGuardAgent",
    "MEM": "MemoryAgent",
    "SCR": "ScreenAgent",
    "SOC": "SocialAgent",
    "MTG": "MeetingAgent",
    "FTN": "FineTuneAgent",
    "THB": "ThumbnailAgent",
    "SEO": "SEOAgent",
    "QNT": "QuarantineAgent",
    "LOG": "LogAnalystAgent",
    "CNC": "CouncilAgent",
    "PLN": "PlannerAgent",
    "EXE": "ExecutorAgent",
    "CRT": "CriticAgent",
    "PMA": "PipelineMemoryAgent",
    "HRM": "HermesAgent",
    "MMA": "MultiModalAgent",
}

AGENT_CATEGORIES = {
    "core": ["TAS", "COD", "RES", "MEM"],
    "intelligence": ["DEC", "CNC", "PLN", "EXE", "CRT", "HRM"],
    "operations": ["COA", "SEC", "SCR", "SOC", "MTG"],
    "creative": ["WRI", "FTN", "THB", "SEO"],
    "system": ["QNT", "LOG", "PMA"],
}

AGENT_CAPABILITIES = {
    "TAS": "system commands, file operations, process management, clipboard, screenshots, open apps, volume control",
    "COD": "code generation, debugging, refactoring, code review, programming in any language",
    "RES": "web search, news, research, information gathering, fact-checking, comparisons",
    "WRI": "writing articles, blogs, stories, poems, emails, creative content, copywriting",
    "COA": "workflow automation, n8n integration, task coordination, pipelines",
    "DEC": "decision analysis, pros/cons, recommendations, evaluations, comparisons",
    "SEC": "security scanning, threat detection, vulnerability assessment, encryption",
    "MEM": "memory recall, remembering past conversations, storing information",
    "SCR": "screenshot analysis, OCR, screen reading, visual analysis",
    "SOC": "social media posting, Twitter, Instagram, LinkedIn management",
    "MTG": "meeting scheduling, calendar management, appointment booking",
    "FTN": "model fine-tuning, training AI models, dataset preparation",
    "THB": "thumbnail design, YouTube thumbnails, cover images, graphics",
    "SEO": "SEO optimization, meta tags, keyword research, ranking analysis",
    "QNT": "file quarantine, suspicious file isolation, threat containment",
    "LOG": "log analysis, error log parsing, system log review, debugging",
    "CNC": "multi-advisor debate, brainstorming, multiple perspectives, consensus",
    "PLN": "task planning, project breakdown, roadmaps, action plans",
    "EXE": "task execution, running subtasks, research + code + file operations",
    "CRT": "code review, quality assessment, feedback, improvement suggestions",
    "PMA": "pipeline memory, context management, previous task results",
    "HRM": "deep reasoning, chain-of-thought, complex problem solving, logic",
}

QUEEN_SYSTEM_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the QUEEN AGENT — central orchestrator of ORBIT.
You are powered by Qwen (local Ollama model). You analyze user requests and delegate to the best agent.

Available agents and their capabilities:
{chr(10).join(f"- {k} ({v}): {AGENT_CAPABILITIES.get(k, 'general tasks')}" for k, v in ALL_AGENTS.items())}

RULES:
1. Analyze the user request carefully
2. Pick the SINGLE BEST agent for the task (or multiple if multi-domain)
3. Return ONLY a JSON object with this exact structure:
{{"plan": ["AGENT_LABEL"], "reasoning": "brief why"}}

Examples:
- "open chrome" -> {{"plan": ["TAS"], "reasoning": "system command to open app"}}
- "what's the news" -> {{"plan": ["RES"], "reasoning": "news research"}}
- "write a poem" -> {{"plan": ["WRI"], "reasoning": "creative writing"}}
- "scan for threats" -> {{"plan": ["SEC"], "reasoning": "security scan"}}
- "remember this" -> {{"plan": ["MEM"], "reasoning": "memory storage"}}
- "schedule a meeting" -> {{"plan": ["MTG"], "reasoning": "calendar/meeting"}}
- "review this code" -> {{"plan": ["CRT"], "reasoning": "code review"}}
- "just chatting" -> {{"plan": [], "reasoning": "casual chat, no agent needed"}}

Be fast and accurate. One JSON response only."""


QUEEN_PLAN_PROMPT = """Analyze this user request and pick the BEST agent.

User request: {text}

Reply with ONLY a JSON object:
{{"plan": ["AGENT_LABEL"], "reasoning": "brief why"}}

Available agents: TAS(system/files), COD(code), RES(research/news), WRI(writing),
COA(workflow), DEC(decisions), SEC(security), MEM(memory), SCR(screen),
SOC(social), MTG(meeting), FTN(finetune), THB(thumbnails), SEO(SEO),
QNT(quarantine), LOG(logs), CNC(council), PLN(planning), EXE(execution),
CRT(review), PMA(pipeline), HRM(reasoning)

For casual chat: {{"plan": [], "reasoning": "chat"}}
JSON only. No explanation."""


class QueenAgent(BaseAgent):
    label = "QNN"
    name = "Queen"
    color = "#ffd700"
    description = "Central orchestrator — plans and delegates to specialized agents"

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self.agents = {}
        self._queue = []
        self._queue_lock = threading.Lock()
        self._active_plan = None
        self._on_agent_activate = None

    def set_agents(self, agent_dict):
        self.agents = agent_dict

    def set_callback(self, on_agent_activate=None):
        self._on_agent_activate = on_agent_activate

    def can_handle(self, text, intent=None, confidence=0.0):
        return True

    def _emit_agent(self, label):
        if self._on_agent_activate:
            self._on_agent_activate(label)

    def _generate_plan(self, text):
        """Generate plan using Qwen (Ollama) as the main planning agent."""
        safe = text.replace("{", "{{").replace("}", "}}")
        prompt = QUEEN_PLAN_PROMPT.format(text=safe)
        try:
            response = self._call_qwen(prompt)
            if not response or response.startswith("[Error"):
                return {"plan": [], "parallel": False, "reasoning": "fallback"}
            response = response.strip()
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()
            plan = json.loads(response)
            if isinstance(plan, dict) and "plan" in plan:
                return plan
        except Exception:
            logger.exception("QueenAgent failed to parse Qwen plan response")
        return {"plan": [], "parallel": False, "reasoning": "chat"}

    def _call_qwen(self, prompt, system_prompt=None, timeout=15):
        """Call Qwen (Ollama) directly for fast local planning."""
        import urllib.request
        import urllib.error

        try:
            payload = json.dumps(
                {
                    "model": DEFAULT_OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt or QUEEN_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 200},
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("message", {}).get("content", "")
        except Exception as e:
            logger.debug("Qwen call failed: %s — falling back to legacy router", e)
            # Fallback to legacy router
            try:
                from ..core.llm import legacy_adapter as llm_router

                return llm_router.generate(
                    prompt, system_prompt=system_prompt or QUEEN_SYSTEM_PROMPT, force_offline=True
                )
            except Exception:
                return None

    def _resolve_agent(self, label, task_text=None):
        agent = self.agents.get(label)
        if agent is None:
            return None
        try:
            from ..core.agent_awareness import get_awareness_manager

            awareness = get_awareness_manager()
            if awareness.journals:
                journal = awareness.get_journal(label)
                if journal:
                    summary = journal.get_performance_summary()
                    overall = summary.get("overall_accuracy", 1.0)
                    if overall < 0.5:
                        try:
                            best_label = max(
                                awareness.journals.keys(),
                                key=lambda n: (
                                    awareness.journals[n].get_performance_summary().get("overall_accuracy", 0)
                                ),
                            )
                        except (ValueError, KeyError):
                            best_label = label
                        if best_label != label and best_label in self.agents:
                            agent = self.agents[best_label]
        except Exception:
            logger.exception("QueenAgent failed to check agent awareness for best match")
        return agent

    def _execute_plan(self, text, plan, context=None):
        agents_to_run = plan.get("plan", [])
        parallel = plan.get("parallel", False)
        synthesize = plan.get("synthesize", True)

        if not agents_to_run:
            return None

        # ── P2: get active trace for span tracking ─────────────────────
        _trace_id = None
        try:
            from ..core.execution_trace import get_execution_tracer

            _tracer = get_execution_tracer()
            # find the active trace (most recent running one)
            for tid, tr in _tracer._active.items():
                if tr.agent_id == "QNN":
                    _trace_id = tid
                    break
        except Exception:
            _trace_id = None

        results = {}
        if parallel:
            threads = []
            lock = threading.Lock()

            def run_agent(label):
                _sid = None
                if _trace_id:
                    try:
                        from ..core.execution_trace import get_execution_tracer

                        _sid = get_execution_tracer().start_span(
                            _trace_id, f"agent:{label}", "agent", agent_id=label, input_preview=text[:200]
                        )
                    except Exception:
                        _sid = None
                agent = self._resolve_agent(label, text)
                if agent:
                    self._emit_agent(label)
                    with lock:
                        prior_results = dict(results)
                    sub_context = dict(context or {})
                    sub_context["prior_results"] = prior_results
                    try:
                        result = agent.execute(text, sub_context)
                    except Exception as e:
                        result = f"[{label} error: {e}]"
                    if _trace_id and _sid:
                        try:
                            from ..core.execution_trace import get_execution_tracer

                            get_execution_tracer().end_span(
                                _trace_id,
                                _sid,
                                status="ok" if not str(result).startswith("[") else "error",
                                output_preview=str(result)[:300],
                            )
                        except Exception:
                            pass
                    with lock:
                        results[label] = result

            for label in agents_to_run:
                t = threading.Thread(target=run_agent, args=(label,), daemon=True)
                t.start()
                threads.append(t)

            for t in threads:
                t.join()
        else:
            for label in agents_to_run:
                _sid = None
                if _trace_id:
                    try:
                        from ..core.execution_trace import get_execution_tracer

                        _sid = get_execution_tracer().start_span(
                            _trace_id, f"agent:{label}", "agent", agent_id=label, input_preview=text[:200]
                        )
                    except Exception:
                        _sid = None
                agent = self._resolve_agent(label, text)
                if agent:
                    self._emit_agent(label)
                    sub_context = dict(context or {})
                    sub_context["prior_results"] = dict(results)
                    try:
                        results[label] = agent.execute(text, sub_context)
                    except Exception as e:
                        results[label] = f"[{label} error: {e}]"
                if _trace_id and _sid:
                    try:
                        from ..core.execution_trace import get_execution_tracer

                        get_execution_tracer().end_span(
                            _trace_id,
                            _sid,
                            status="ok" if label in results and not str(results[label]).startswith("[") else "error",
                            output_preview=str(results.get(label, ""))[:300],
                        )
                    except Exception:
                        pass

        if synthesize and len(results) > 0:
            synthesis = self._critique_and_synthesize(text, results, agents_to_run)
            self._submit_feedback(text, results, agents_to_run)
            return synthesis

        combined = []
        for label in agents_to_run:
            if label in results:
                combined.append(f"[{label}]: {results[label]}")
        return "\n\n".join(combined)

    def _critique_and_synthesize(self, text, results, agent_labels):
        critic = self.agents.get("CRT")

        reviewed = {}
        for label in agent_labels:
            if label in results and critic:
                try:
                    verdict = critic.review(
                        {"type": "general", "description": text[:200]},
                        str(results[label])[:2000],
                    )
                    reviewed[label] = verdict
                except Exception:
                    reviewed[label] = {"quality": "pass", "score": 0.5}

        parts = []
        for label in agent_labels:
            if label not in results:
                continue
            agent = self.agents.get(label)
            name = agent.name if agent else label
            quality = reviewed.get(label, {}).get("quality", "unknown") if label in reviewed else "unknown"
            score = reviewed.get(label, {}).get("score", 0.5) if label in reviewed else 0.5
            badge = "✅" if score >= 0.7 else "⚠️" if score >= 0.4 else "❌"
            parts.append(f"**{name}** [{badge} quality={quality}, score={score:.2f}]:\n{results[label]}")

        synthesis_prompt = (
            f"User request: {text}\n\n"
            f"Multiple agents produced these results:\n"
            f"{chr(10).join(parts)}\n\n"
            f"Synthesize these into a single coherent response for the user. "
            f"Include quality indicators. Combine naturally, avoid repetition."
        )

        try:
            result = self._call_qwen(synthesis_prompt, timeout=20)
            if result:
                return f"🤖 **Multi-Agent Analysis**\n\n{result}"
        except Exception:
            pass
        return "\n\n".join(str(v) for v in results.values())

    def _submit_feedback(self, text, results, agent_labels):
        try:
            from ..core.self_improvement import get_self_improvement
            from ..core.agent_awareness import get_awareness_manager
            from ..core.agent_awareness import Decision as AwarenessDecision

            si = get_self_improvement()
            awareness = get_awareness_manager()
            for label in agent_labels:
                result = results.get(label, "")
                r = result if isinstance(result, str) else str(result or "")
                score = 0.5
                correct = True
                if r and not _is_agent_error(r) and len(r) > 50:
                    score = 0.8
                elif r and _is_agent_error(r):
                    score = 0.1
                    correct = False
                si.add_feedback(
                    agent_id=label,
                    task_type="queen_dispatch",
                    task_input=text[:200],
                    task_output=str(result)[:200],
                    score=score,
                    comment="auto-feedback from queen synthesis",
                )
                try:
                    awareness.register_agent(label, [label.lower()])
                    awareness.log_decision(
                        label,
                        AwarenessDecision(
                            domain=label.lower(),
                            task=str(text)[:100],
                            prediction="execute",
                            actual_outcome="success" if correct else "failure",
                            correct=correct,
                        ),
                    )
                except Exception:
                    logger.exception("QueenAgent failed to log awareness decision in feedback loop")
        except Exception:
            logger.exception("QueenAgent feedback loop failed")

    def _run(self, text, context=None):
        self._emit_agent("QNN")

        # ── P2: start execution trace ──────────────────────────────────
        _trace_id = None
        try:
            from ..core.execution_trace import get_execution_tracer

            _tracer = get_execution_tracer()
            _trace_id = _tracer.start_trace(query=text[:500], agent_id="QNN")
        except Exception:
            _trace_id = None

        try:
            # PRIMARY: SmartRouter auto-detection (zero LLM cost, instant)
            try:
                from ..core.smart_router import auto_route

                route = auto_route(text)
                if route.agents and route.agents[0].score > 0.5:
                    best_label = route.agents[0].label
                    agent = self._resolve_agent(best_label, text)
                    if agent:
                        self._emit_agent(best_label)
                        if _trace_id:
                            try:
                                from ..core.execution_trace import get_execution_tracer

                                get_execution_tracer().start_span(
                                    _trace_id,
                                    f"agent:{best_label}",
                                    "agent",
                                    agent_id=best_label,
                                    input_preview=text[:200],
                                )
                            except Exception:
                                pass
                        result = agent.execute(text, context)
                        if result and not _is_agent_error(result):
                            if _trace_id:
                                try:
                                    from ..core.execution_trace import get_execution_tracer

                                    _t = get_execution_tracer()
                                    # end the agent span
                                    for s in (
                                        _t._active.get(_trace_id, object()).spans
                                        if hasattr(_t._active.get(_trace_id, object()), "spans")
                                        else []
                                    ):
                                        if s.get("status") == "running":
                                            _t.end_span(
                                                _trace_id, s["id"], status="ok", output_preview=str(result)[:300]
                                            )
                                            break
                                    _t.end_trace(_trace_id, status="ok", result=str(result)[:500])
                                except Exception:
                                    pass
                            return result
            except Exception:
                logger.debug("SmartRouter primary path failed")

            # SECONDARY: Qwen (Ollama) LLM planning for complex/ambiguous tasks
            try:
                intent = self._detect_intent_simple(text)
            except Exception:
                return "Let me try to help with that."

            if intent == "chat":
                try:
                    direct_agents = [a for a in self.agents.values() if a.can_handle(text, "chat", 0.3)]
                    if direct_agents:
                        return direct_agents[0].execute(text, context)
                except Exception:
                    logger.exception("QueenAgent failed to find chat handler")
                if _trace_id:
                    try:
                        from ..core.execution_trace import get_execution_tracer

                        get_execution_tracer().end_trace(_trace_id, status="ok", result="chat")
                    except Exception:
                        pass
                return None

            # Use Qwen to plan which agents to use
            plan = self._generate_plan(text)
            self._active_plan = plan
            result = self._execute_plan(text, plan, context)
            if _trace_id:
                try:
                    from ..core.execution_trace import get_execution_tracer

                    get_execution_tracer().end_trace(_trace_id, status="ok", result=str(result)[:500] if result else "")
                except Exception:
                    pass
            return result
        except Exception as exc:
            if _trace_id:
                try:
                    from ..core.execution_trace import get_execution_tracer

                    get_execution_tracer().end_trace(_trace_id, status="error", error=str(exc)[:300])
                except Exception:
                    pass
            raise

    def _detect_intent_simple(self, text):
        """Fast intent detection using SmartRouter (zero LLM cost)."""
        try:
            from ..core.smart_router import auto_route

            route = auto_route(text)
            if route.agents and route.agents[0].score > 0.5:
                return route.intent
        except Exception:
            pass
        # Fallback: keyword matching
        text_lower = text.lower()
        try:
            from ..assistant import TOOL_DEFINITIONS

            scores = {}
            for intent, config in TOOL_DEFINITIONS.items():
                score = sum(1 for kw in config["keywords"] if kw in text_lower)
                if score > 0:
                    scores[intent] = score * config.get("weight", 1.0)
            if scores:
                return max(scores, key=scores.get)
        except Exception:
            pass
        return "chat"

    def get_plan(self):
        return self._active_plan

    def get_queue_status(self):
        with self._queue_lock:
            return list(self._queue)
