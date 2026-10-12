# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""Utility agents — quarantine, log analysis, coordination, decision, security, memory, Hermes."""

import logging
import re
from pathlib import Path

from ..config import ORBIT_SYSTEM_PROMPT
from ..core.llm import legacy_adapter as llm_router
from .. import memory as memory_module
from ..core.memory_store import get_shared_memory, share_memory, recall_shared, cleanup_stale_memories
from .base import BaseAgent

logger = logging.getLogger(__name__)


class QuarantineAgent(BaseAgent):
    label = "QNT"
    name = "Quarantine"
    color = "#eab308"
    description = "Isolate suspicious files, manage quarantine, threat response"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the QUARANTINE specialist.
Your job: Isolate suspicious files, manage quarantine zone, respond to threats.
Work with SecurityTool to move suspicious files to quarantine and analyze them."""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._tool = None
        self._file_tool = None

    def _get_tool(self):
        if self._tool is None:
            from ..tools import SecurityTool

            self._tool = SecurityTool()
        return self._tool

    def _get_file_tool(self):
        if self._file_tool is None:
            from ..tools.file_tools import FileTools

            self._file_tool = FileTools()
        return self._file_tool

    def _quarantine_dir(self):
        try:
            from ..core.policy_engine import get_workspace_root

            return get_workspace_root() / "orbit_data" / "quarantine"
        except Exception:
            return Path.home() / "orbit_quarantine"

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = ["quarantine", "isolate", "suspicious file", "infected", "malware file", "contain", "containment"]
        text_lower = text.lower()
        return sum(1 for kw in keywords if kw in text_lower) >= 2

    def _run(self, text, context=None):
        text_lower = text.lower()
        if "quarantine" in text_lower and ("move" in text_lower or "isolate" in text_lower or "file" in text_lower):
            m = re.search(r"(?:quarantine|isolate|move)\s+(?:file\s+)?[`\"']?([^\s`\"']+)", text, re.IGNORECASE)
            if m:
                file_path = m.group(1)
                quarantine_dir = self._quarantine_dir()
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                ft = self._get_file_tool()
                dest = quarantine_dir / Path(file_path).name
                copy_result = ft.copy(file_path, str(dest))
                if not copy_result or not isinstance(copy_result, dict):
                    return f"Quarantine failed: copy returned {copy_result}"
                if copy_result.get("success"):
                    del_result = ft.delete(file_path)
                    if not del_result or not del_result.get("success"):
                        note = ""
                        if isinstance(del_result, dict) and del_result.get("error"):
                            note = f" (original delete blocked: {del_result['error']})"
                        return f"File copied to quarantine: {file_path} -> {dest}{note}"
                    return f"File quarantined: {file_path} -> {dest}"
                return f"Quarantine failed: {copy_result.get('error')}"
            return "Which file should I quarantine? Usage: quarantine file 'path'"
        if "list quarantine" in text_lower or "show quarantine" in text_lower:
            quarantine_dir = self._quarantine_dir()
            if quarantine_dir.exists():
                ft = self._get_file_tool()
                list_result = ft.list_dir(str(quarantine_dir))
                if not list_result or not isinstance(list_result, dict):
                    return "Failed to list quarantine directory."
                items = list_result.get("items", [])
                if items:
                    lines = [f"Quarantine zone ({len(items)} files):"]
                    for item in items:
                        lines.append(f"  - {item['name']} ({item['size']} bytes)")
                    return "\n".join(lines)
                return "Quarantine zone is empty."
            return "Quarantine directory doesn't exist yet."
        if "restore" in text_lower:
            m = re.search(r"restore\s+[`\"']?([^\s`\"']+)", text, re.IGNORECASE)
            if m:
                filename = m.group(1)
                quarantine_dir = self._quarantine_dir()
                src = quarantine_dir / filename
                if src.exists():
                    ft = self._get_file_tool()
                    result = ft.copy(str(src), filename)
                    if result and isinstance(result, dict) and result.get("success"):
                        ft.delete(str(src))
                        return f"File restored: {filename}"
                    return f"Restore failed: {result.get('error')}"
                return f"File not found in quarantine: {filename}"
            return "Which file should I restore? Usage: restore 'filename'"
        tool = self._get_tool()
        if tool:
            result = tool.handle_natural(text)
            if isinstance(result, dict):
                return result.get("result", str(result))
            return result
        return "Quarantine agent: security tool unavailable."


class LogAnalystAgent(BaseAgent):
    label = "LOG"
    name = "Log Analyst"
    color = "#6366f1"
    description = "Log analysis, error pattern detection, debugging assistance"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the LOG ANALYST specialist - the detective of the system.

Your capabilities:
- Parse and analyze application logs (syslog, event logs, custom formats)
- Detect error patterns and root causes
- Trace execution flow through log entries
- Identify performance bottlenecks from timing data
- Correlate events across multiple log sources
- Suggest fixes based on error patterns
- Generate summary reports from log data

Rules:
- ALWAYS use FileTools to read log files
- ALWAYS use ComputerTool for real-time log tailing (PowerShell Get-Content)
- For error analysis: find the first occurrence, trace upstream, identify root cause
- For pattern detection: look for repeated errors, frequency spikes, time correlations
- Present findings with timestamps, error codes, and stack traces
- Suggest specific fixes, not generic advice
- For "what went wrong" questions: trace the error chain
- For "find errors in X.log": scan and categorize by severity
- For "performance issues": look for slow operations, timeouts, resource spikes"""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._file_tool = None
        self._computer_tool = None

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

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "log",
            "logs",
            "error log",
            "debug",
            "stacktrace",
            "traceback",
            "crash",
            "analyze log",
            "log analysis",
        ]
        text_lower = text.lower()
        return sum(1 for kw in keywords if kw in text_lower) >= 1

    def _run(self, text, context=None):
        text_lower = text.lower()
        log_content = None

        file_match = re.search(r"([^\s`\"']+\.(?:log|txt|out|err))", text, re.IGNORECASE)
        if file_match:
            filepath = file_match.group(1)
            tool = self._get_file_tool()
            if tool:
                try:
                    result = tool.read(filepath)
                    if result and isinstance(result, dict) and "content" in result:
                        lines = result["content"].split("\n")
                        if len(lines) > 500:
                            lines = lines[-500:]
                            log_content = "\n".join(lines)
                            log_content = (
                                f"[Last 500 lines of {len(result['content'].split(chr(10)))} total]\n{log_content}"
                            )
                        else:
                            log_content = result["content"]
                    elif result and isinstance(result, dict) and result.get("error"):
                        return f"Could not read {filepath}: {result.get('error')}"
                except Exception as e:
                    return f"Could not read {filepath}: {e}"

        if not log_content and ("recent" in text_lower or "last" in text_lower or "tail" in text_lower):
            file_tool = self._get_file_tool()
            if file_tool:
                try:
                    import glob as _glob
                    import os as _os

                    log_files = _glob.glob("*.log") + _glob.glob("**/*.log", recursive=True)
                    if log_files:
                        newest = max(log_files, key=lambda f: _os.path.getmtime(f))
                        result = file_tool.read(newest)
                        if result and isinstance(result, dict) and "content" in result:
                            lines = result.get("content", "").splitlines()
                            log_content = "\n".join(lines[-200:])
                except Exception:
                    pass

        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        if log_content:
            sys_prompt += f"\n\n[Log Content]\n{log_content}"

        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)


class CoordinationAgent(BaseAgent):
    label = "COA"
    name = "Coordination"
    color = "#10b981"
    description = "Workflow automation, multi-step tasks, n8n"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the COORDINATION specialist.
Your job: Automate workflows, connect systems, orchestrate multi-step tasks.
Use n8n, webhooks, and APIs to connect different services."""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._tool = None

    def _get_tool(self):
        if self._tool is None:
            from ..tools import N8nTool

            self._tool = N8nTool()
        return self._tool

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "workflow",
            "automation",
            "webhook",
            "trigger",
            "n8n",
            "schedule",
            "cron",
            "automate",
            "integrate",
            "connect",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if intent == "n8n":
            return score >= 1
        return score >= 2

    def _run(self, text, context=None):
        tool = self._get_tool()
        if tool:
            result = tool.handle_natural(text)
            return result or "Coordination task completed (no detailed response)."
        return "Coordination agent: n8n tool unavailable."


class DecisionAgent(BaseAgent):
    label = "DEC"
    name = "Decision"
    color = "#ef4444"
    description = "Analysis, strategy, recommendations"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the DECISION specialist - the strategist of the system.

Your capabilities:
- Analyze complex situations and break down problems
- Compare options with structured pros/cons
- Provide data-driven recommendations
- Assess risks and trade-offs
- Create decision matrices
- Evaluate trade-offs between competing priorities
- Weigh short-term vs long-term implications

Rules:
- Use DecisionEngine for structured analysis when available
- Present comparisons as tables when possible
- Rate confidence level (high/medium/low) for each recommendation
- Consider: cost, time, risk, scalability, maintainability
- For "should I X" questions: analyze both sides, give a clear recommendation
- For "compare X vs Y": create a comparison table with weighted criteria
- For "what are the risks": list risks with severity and mitigation
- Be objective - present facts, not opinions
- If data is insufficient, say what additional info would help"""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from ..core.decision_engine import DecisionEngine

            self._engine = DecisionEngine()
        return self._engine

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "decide",
            "recommend",
            "suggest",
            "opinion",
            "best",
            "compare",
            "analysis",
            "analyze",
            "strategy",
            "plan",
            "should i",
            "which",
            "pros and cons",
            "weigh",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        return score >= 2

    def _run(self, text, context=None):
        text_lower = text.lower()
        if "analyze" in text_lower or "decide" in text_lower or "pros and cons" in text_lower or "weigh" in text_lower:
            engine = self._get_engine()
            options_match = re.findall(
                r"(?:option|choice|alternative)\s*(\d+)?\s*[:=]\s*[`\"']?([^,`\"']+)", text, re.IGNORECASE
            )
            if not options_match:
                options_match = re.findall(r"[`\"']([^`\"']+)[`\"']", text)
            if options_match:
                options = [{"name": o[1] if isinstance(o, tuple) else o} for o in options_match]
            else:
                options = [{"name": "Option A"}, {"name": "Option B"}]
            question = re.sub(r"\b(analyze|decide|pros|cons|weigh|between|which|should)\b", "", text_lower).strip()[
                :200
            ]
            result = engine.analyze(question or text, options)
            winner = result.get("winner", "N/A")
            score = result.get("winner_score", 0)
            confidence = result.get("confidence", 0)
            rec = result.get("recommendation", "")
            lines = [
                f"Decision Analysis: {question or text[:100]}",
                f"Winner: {winner} (Score: {score:.1f}/100)",
                f"Confidence: {confidence:.0f}%",
                f"\nRecommendation: {rec}",
                "\nOptions ranked:",
            ]
            for i, opt in enumerate(result.get("options", []), 1):
                lines.append(f"  {i}. {opt['name']} - {opt['weighted_total']:.1f}/100 (Risk: {opt['risk']})")
            return "\n".join(lines)
        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)


class SecurityAgent(BaseAgent):
    label = "SEC"
    name = "Security"
    color = "#f97316"
    description = "System security, threat detection, firewall"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the SECURITY specialist - the shield of the system.

Your capabilities:
- Scan for malware, viruses, and security threats
- Check open ports and network vulnerabilities
- Analyze firewall rules and configurations
- Audit file permissions and access controls
- Detect suspicious processes and network activity
- Review system security settings
- Assess password strength and authentication

Rules:
- ALWAYS use SecurityTool for actual scans
- ALWAYS use CAISecurityTool for AI-powered threat analysis
- Report findings with severity levels: CRITICAL/HIGH/MEDIUM/LOW/INFO
- Provide actionable remediation steps for each finding
- For "scan" requests: run the scan and present results
- For "is my system safe" questions: run a quick health check
- For port scanning: show open ports and associated services
- Never ignore findings - even "no threats found" is valuable information
- If a tool fails, explain what happened and suggest alternatives"""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._tool = None

    def _get_tool(self):
        if self._tool is None:
            from ..tools import SecurityTool

            self._tool = SecurityTool()
        return self._tool

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = ["security", "scan", "virus", "threat", "port", "firewall", "defender", "malware", "hack", "protect"]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if intent == "security":
            return score >= 1
        return score >= 2

    def _run(self, text, context=None):
        tool = self._get_tool()
        if tool:
            result = tool.handle_natural(text)
            if isinstance(result, dict):
                return result.get("result", str(result))
            return result
        return "Security agent unavailable."


class SecurityGuardAgent(BaseAgent):
    label = "SECG"
    name = "SecurityGuard"
    color = "#dc2626"
    description = "Defends ORBIT itself — codebase scans, defense monitoring, auto-fix, incident alerts"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the SECURITY GUARD.
Your job: Keep ORBIT itself secure. Continuously scan the codebase for secrets and
dangerous patterns, verify all security defenses are active (API auth, rate limiting,
security headers, ToolGate sandbox, secret redaction, kill switch), and raise alerts.
Report clear, actionable findings. Never disable a defense."""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._guard = None

    def _get_guard(self):
        if self._guard is None:
            from ..core.security_guard import get_security_guard

            self._guard = get_security_guard()
        return self._guard

    def can_handle(self, text, intent=None, confidence=0.0):
        strong = [
            "guard",
            "codebase",
            "defense check",
            "defense status",
            "security scan",
            "security audit",
            "hardening",
            "protect orbit",
            "secure orbit",
            "autofix",
            "auto-fix",
            "self-security",
            "secret scan",
            "incidents",
        ]
        weak = [
            "defense",
            "security",
            "scan",
            "monitor",
            "incident",
            "hardening",
        ]
        text_lower = text.lower()
        if any(kw in text_lower for kw in strong):
            return True
        score = sum(1 for kw in weak if kw in text_lower)
        if intent == "security":
            return score >= 1
        return score >= 2

    def _run(self, text, context=None):
        guard = self._get_guard()
        text_lower = text.lower()

        if any(k in text_lower for k in ["scan", "codebase", "secrets", "secret scan", "scan code"]):
            result = guard.scan_codebase()
            findings = result.get("findings", [])
            if not findings:
                return (
                    f"🛡️ Codebase scan clean.\n"
                    f"Scanned {result.get('files_scanned', 0)} files, 0 findings. "
                    f"Scanned at {result.get('scanned_at', '')[:19]}."
                )
            lines = [f"⚠️ Codebase scan found {result.get('count', 0)} issue(s):"]
            for f in findings[:10]:
                sev = f.get("severity", "medium")
                icon = "🔴" if sev == "critical" else "🟠"
                lines.append(f"{icon} [{sev.upper()}] {f.get('file')}:{f.get('line')} — {f.get('name')}")
            if len(findings) > 10:
                lines.append(f"  ... and {len(findings) - 10} more (see /api/security-guard/scan)")
            return "\n".join(lines)

        if any(k in text_lower for k in ["defense", "defenses", "status", "check", "health", "hardening"]):
            result = guard.check_defenses()
            lines = [f"🛡️ Defense status: {result.get('passed')}/{result.get('total')} active"]
            for c in result.get("checks", []):
                mark = "✅" if c.get("ok") else "❌"
                lines.append(f"{mark} {c.get('name')}: {c.get('detail')}")
            return "\n".join(lines)

        if any(k in text_lower for k in ["incident", "log", "alerts", "alarm"]):
            incidents = guard.incidents(20)
            if not incidents:
                return "📋 No security incidents logged."
            lines = [f"📋 Last {len(incidents)} incidents:"]
            for inc in incidents:
                lines.append(f"  [{inc.get('severity')}] {inc.get('message', '')[:120]}")
            return "\n".join(lines)

        if any(k in text_lower for k in ["autofix", "auto-fix", "fix", "remediate"]):
            result = guard.auto_fix()
            lines = [f"🔧 Auto-fix ran {result.get('count', 0)} remediation(s):"]
            for fx in result.get("fixes", []):
                mark = "✅" if fx.get("applied") else "⏸️"
                lines.append(f"{mark} {fx.get('item')}: {fx.get('detail')}")
            return "\n".join(lines)

        if any(k in text_lower for k in ["monitor", "watch", "start guard", "background"]):
            started = guard.start_monitor()
            return "🛡️ Background security monitoring started." if started else "🛡️ Monitoring already running."

        if any(k in text_lower for k in ["stop", "halt guard"]):
            guard.stop_monitor()
            return "🛡️ Background security monitoring stopped."

        # Default: full report
        report = guard.run_full_check()
        verdict = report.get("verdict", "unknown")
        code = report.get("codebase", {})
        defs = report.get("defenses", {})
        emoji = {"secure": "🛡️", "defense_gap": "⚠️", "high_findings": "🟠", "critical_findings": "🔴"}.get(verdict, "🛡️")
        return (
            f"{emoji} SecurityGuard full report — verdict: {verdict}\n"
            f"Codebase: {code.get('count', 0)} findings across {code.get('files_scanned', 0)} files "
            f"({code.get('critical', 0)} critical, {code.get('high', 0)} high)\n"
            f"Defenses: {defs.get('passed', 0)}/{defs.get('total', 0)} active\n"
            f"Say 'security scan', 'defense status', or 'incidents' for details."
        )


class MemoryAgent(BaseAgent):
    label = "MEM"
    name = "Memory"
    color = "#8b5cf6"
    description = "Cross-agent memory sharing, relevance scoring, stale cleanup"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the MEMORY specialist - the long-term memory of the system.

Your capabilities:
- Store and recall information across all agents
- Search memories by relevance and recency
- Share context between agents via shared memory pool
- Clean stale entries and consolidate similar memories
- Track user preferences, corrections, and patterns
- Provide context-aware responses based on past interactions

Rules:
- Search memory before responding - check if this question was asked before
- Reference previous interactions when relevant
- Store new information with proper tags and categories
- When recalling, rank by relevance + recency
- Clean up duplicate or stale entries
- For "remember X" requests: store with clear tags
- For "what did we discuss about Y" questions: search and summarize
- For "forget X" requests: remove from memory
- Maintain a timeline of important events and decisions"""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._pool = get_shared_memory()

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "remember",
            "memory",
            "recall",
            "forget",
            "clear",
            "previous",
            "history",
            "yaad",
            "bhool",
            "purani",
            "share memory",
            "cleanup",
            "stale",
            "relevance",
            "search memory",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if intent == "memory":
            return score >= 1
        return score >= 2

    def _run(self, text, context=None):
        text_lower = text.lower()

        # ── cleanup stale memories ──────────────────────────────────────────
        if "cleanup" in text_lower or "stale" in text_lower:
            result = cleanup_stale_memories()
            return f"🧹 Memory cleanup done.\nPruned: {result['pruned']} entries | Remaining: {result['remaining']}"

        # ── clear all memories ──────────────────────────────────────────────
        if "clear" in text_lower or ("forget" in text_lower and "everything" in text_lower):
            memory_module.clear()
            return "🧹 Conversation memory cleared."

        # ── search with relevance scoring ───────────────────────────────────
        if "search memory" in text_lower or "recall" in text_lower or "history" in text_lower:
            query = text.replace("search memory", "").replace("recall", "").replace("history", "").strip()
            if not query:
                query = text

            results = recall_shared(
                agent_id=self.label,
                query=query,
                namespaces=["conversation", "code", "task", "global"],
                limit=5,
            )

            if results:
                lines = ["🧠 Relevant memories (scored by relevance):\n"]
                for r in results:
                    score = r.get("relevance_score", 0)
                    src = r.get("source_agent", "?")
                    val = r.get("value", {})
                    summary = val.get("summary", str(val)[:120]) if isinstance(val, dict) else str(val)[:120]
                    lines.append(f"  [{score:.2f}] ({src}) {summary}")

                recent = memory_module.get_recent(3)
                if recent:
                    lines.append("\n📜 Recent conversation:")
                    for m in recent:
                        lines.append(f"  - {m['user'][:50]} -> {m['assistant'][:50]}")
                return "\n".join(lines)
            else:
                recent = memory_module.get_recent(5)
                if recent:
                    return "🧠 No cross-agent matches. Recent conversation:\n" + "\n".join(
                        [f"  - {m['user'][:50]} -> {m['assistant'][:50]}" for m in recent]
                    )
                return "🧠 No memories found."

        # ── memory stats ────────────────────────────────────────────────────
        if "stats" in text_lower or "memory stats" in text_lower:
            stats = self._pool.stats()
            lines = [
                "📊 Memory Stats:",
                f"  Total: {stats['total_memories']} entries",
                f"  Avg Strength: {stats['avg_strength']:.2f}",
                "  By Namespace:",
            ]
            for ns, count in stats.get("by_namespace", {}).items():
                lines.append(f"    {ns}: {count}")
            lines.append("  By Agent:")
            for ag, count in stats.get("by_agent", {}).items():
                lines.append(f"    {ag}: {count}")
            return "\n".join(lines)

        # ── save to shared memory (other agents call this) ──────────────────
        if "share:" in text_lower or "save to memory:" in text_lower:
            raw = text.split(":", 1)[-1].strip() if ":" in text else text
            entry = share_memory(
                agent_id=self.label,
                namespace="global",
                key=f"note_{len(self.history)}",
                value={"summary": raw, "original_query": text},
                tags=["manual", "user"],
            )
            return f"💾 Memory saved: {entry.key} (id={entry.id})"

        # ── default: LLM-augmented recall ───────────────────────────────────
        mem_context = memory_module.get_context_str(5)
        shared_ctx = recall_shared(agent_id=self.label, query=text, limit=3)
        shared_str = ""
        if shared_ctx:
            shared_str = "\nCross-agent memories:\n" + "\n".join(
                f"  [{r['source_agent']}] {r.get('value', {}).get('summary', str(r.get('value', ''))[:100])}"
                for r in shared_ctx
            )

        sys_prompt = self.DEFAULT_PROMPT
        if mem_context:
            sys_prompt += f"\n\n[Conversation Memory]\n{mem_context}"
        if shared_str:
            sys_prompt += f"\n\n{shared_str}"
        if context:
            sys_prompt += f"\n\n[Additional Context]\n{context}"
        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)


class HermesAgent(BaseAgent):
    label = "HRM"
    name = "Hermes"
    color = "#ff6b35"
    description = "Nous Hermes — creative reasoning, uncensored instruct, chain-of-thought via OpenRouter"

    HERMES_MODELS = [
        "nousresearch/hermes-3-llama-3.1-405b",
        "nousresearch/hermes-3-llama-3.1-70b",
        "nousresearch/hermes-2-pro-llama-3-8b",
    ]

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are HERMES — a specialized reasoning agent powered by Nous Hermes.
Your job: Deep reasoning, creative problem-solving, structured chain-of-thought.
Use step-by-step reasoning. Be thorough but concise.
If the task needs uncensored or unrestricted output, handle it naturally."""

    def __init__(self, assistant=None):
        super().__init__(assistant)

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "hermes",
            "reason deeply",
            "chain of thought",
            "think step by step",
            "creative reasoning",
            "uncensored",
            "unrestricted",
            "deep analysis",
            "think through",
            "reason out",
            "complex problem",
            "think deeply",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if intent == "reasoning":
            return True
        return score >= 1

    def _run(self, text, context=None):
        import requests as _requests

        api_key = ""
        try:
            from ..config import decrypt_env

            api_key = decrypt_env("OPENROUTER_API_KEY", "")
        except Exception:
            pass

        if not api_key:
            sys_prompt = self.DEFAULT_PROMPT
            if context:
                sys_prompt += f"\n\n[Context]\n{context}"
            return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)

        messages = [{"role": "system", "content": self.DEFAULT_PROMPT}]
        if context:
            messages.append({"role": "system", "content": f"[Context]\n{context}"})
        if self.history:
            for msg in self.history[-6:]:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        messages.append({"role": "user", "content": text})

        for model in self.HERMES_MODELS:
            try:
                base_url = ""
                try:
                    from ..config import decrypt_env

                    base_url = decrypt_env("OPENROUTER_BASE_URL", "").strip()
                except Exception:
                    pass
                url = (
                    (base_url.rstrip("/") + "/chat/completions")
                    if base_url
                    else "https://openrouter.ai/api/v1/chat/completions"
                )
                resp = _requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "http://localhost:3000",
                        "X-Title": "ORBIT Hermes",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 2048,
                    },
                    timeout=60,
                )
                if resp.status_code == 200:
                    result = resp.json()["choices"][0]["message"]["content"]
                    with self._lock:
                        self.history.append({"role": "user", "content": text})
                        self.history.append({"role": "assistant", "content": result})
                        if len(self.history) > 20:
                            self.history = self.history[-20:]
                    return result
            except Exception as e:
                logger.warning("Hermes model %s failed: %s", model, e)
                continue

        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)
