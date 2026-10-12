# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
Smart Auto-Router — deterministic agent + feature detection.

Analyzes user input and returns:
  - Best matching agent(s) with confidence scores
  - Matching feature panel(s) to auto-open
  - Intent classification

Zero LLM cost — pure keyword/phrase matching with weighted scoring.
"""

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  QUERY EXPANSION — synonyms for better matching
# ═══════════════════════════════════════════════════════════════════════════════

SYNONYM_MAP: Dict[str, List[str]] = {
    # Code
    "build": ["compile", "make", "create"],
    "write": ["create", "generate", "draft"],
    "fix": ["debug", "repair", "resolve", "patch"],
    "improve": ["optimize", "enhance", "refine"],
    "explain": ["describe", "clarify", "elaborate"],
    "test": ["verify", "validate", "check"],
    # Files
    "delete": ["remove", "erase", "destroy"],
    "move": ["transfer", "relocate", "shift"],
    "copy": ["duplicate", "clone", "replicate"],
    "find": ["search", "locate", "discover"],
    "show": ["display", "present", "reveal"],
    # System
    "stop": ["halt", "terminate", "kill"],
    "start": ["launch", "begin", "initiate"],
    "check": ["inspect", "examine", "review"],
    "update": ["upgrade", "refresh", "patch"],
    # Research
    "research": ["investigate", "explore", "study"],
    "analyze": ["examine", "evaluate", "assess"],
    "summarize": ["condense", "outline", "abstract"],
    # Writing
    "compose": ["draft", "author", "write"],
    "edit": ["modify", "revise", "refine"],
    "translate": ["convert", "localize"],
}

# ═══════════════════════════════════════════════════════════════════════════════
#  DATA TYPES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class AgentMatch:
    label: str
    score: float
    reason: str


@dataclass
class FeatureMatch:
    panel: str
    command: str
    score: float
    reason: str


@dataclass
class RouteResult:
    agents: List[AgentMatch] = field(default_factory=list)
    features: List[FeatureMatch] = field(default_factory=list)
    intent: str = "chat"
    confidence: float = 0.0
    is_tool_command: bool = False
    routing: str = "llm_only"  # "direct_agent" | "agent_with_llm" | "llm_only"
    negated: bool = False  # True if negation detected before matched keyword

    def best_agent(self) -> Optional[str]:
        if self.agents:
            return self.agents[0].label
        return None

    def best_feature(self) -> Optional[str]:
        if self.features:
            return self.features[0].command
        return None

    def to_dict(self) -> dict:
        return {
            "agents": [{"label": a.label, "score": a.score, "reason": a.reason} for a in self.agents],
            "features": [{"panel": f.panel, "command": f.command, "score": f.score} for f in self.features],
            "intent": self.intent,
            "confidence": self.confidence,
            "is_tool_command": self.is_tool_command,
            "routing": self.routing,
            "negated": self.negated,
            "best_agent": self.best_agent(),
            "best_feature": self.best_feature(),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  NEGATION DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

_NEGATION_WORDS = frozenset(["don't", "do not", "never", "stop", "cancel", "avoid", "skip", "no", "not", "without"])


def _has_negation(text: str, keyword_pos: int) -> bool:
    """Check if a negation word appears within 5 words before keyword_pos."""
    preceding = text[:keyword_pos].split()[-6:]
    for i in range(len(preceding)):
        if preceding[i] in _NEGATION_WORDS:
            return True
        if i + 1 < len(preceding) and f"{preceding[i]} {preceding[i + 1]}" in _NEGATION_WORDS:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
#  AGENT KEYWORD MAP — weighted phrases per agent
# ═══════════════════════════════════════════════════════════════════════════════

AGENT_KEYWORDS: Dict[str, List[Tuple[str, float]]] = {
    "TAS": [
        ("open", 1.5),
        ("launch", 1.5),
        ("run", 1.2),
        ("execute", 1.2),
        ("file", 1.0),
        ("folder", 1.0),
        ("directory", 1.0),
        ("list files", 1.5),
        ("read file", 2.0),
        ("write file", 2.0),
        ("edit file", 2.0),
        ("copy file", 2.0),
        ("delete file", 2.0),
        ("move file", 2.0),
        ("process", 1.0),
        ("kill process", 2.0),
        ("task manager", 1.5),
        ("clipboard", 1.5),
        ("copy", 0.8),
        ("paste", 0.8),
        ("screenshot", 1.5),
        ("screen capture", 1.5),
        ("system info", 1.5),
        ("specs", 1.2),
        ("hardware", 1.0),
        ("mouse", 1.0),
        ("keyboard", 1.0),
        ("click", 0.8),
        ("type", 0.5),
        ("scroll", 0.8),
        ("window", 0.8),
        ("minimize", 1.2),
        ("maximize", 1.2),
        ("close window", 1.5),
        ("volume", 1.0),
        ("brightness", 1.0),
        ("install", 1.0),
        ("uninstall", 1.0),
        ("update", 0.8),
        ("shutdown", 1.5),
        ("restart", 1.5),
        ("sleep", 1.0),
        ("khol", 1.5),
        ("kholo", 1.5),
        ("chala", 1.2),
        ("band", 1.0),
    ],
    "COD": [
        ("code", 1.2),
        ("program", 1.0),
        ("function", 1.0),
        ("class", 0.8),
        ("write code", 2.0),
        ("generate code", 2.0),
        ("coding", 1.5),
        ("python", 1.2),
        ("javascript", 1.2),
        ("typescript", 1.2),
        ("html", 1.0),
        ("css", 1.0),
        ("sql", 1.0),
        ("api", 0.8),
        ("debug", 1.5),
        ("fix code", 2.0),
        ("refactor", 1.5),
        ("algorithm", 1.2),
        ("data structure", 1.2),
        ("compile", 1.2),
        ("syntax", 1.0),
        ("import", 0.8),
        ("module", 0.8),
        ("library", 0.8),
        ("package", 0.8),
        ("script", 1.0),
        ("snippet", 1.0),
        ("implementation", 1.0),
        ("code review", 2.0),
        ("optimize code", 1.5),
        ("build", 1.0),
        ("create", 0.8),
        ("react", 1.2),
        ("component", 1.0),
        ("convert", 1.0),
        ("translate", 0.8),
        ("compress", 0.8),
        ("image", 0.5),
        ("rest api", 1.5),
        ("webhook", 0.8),
        ("endpoint", 1.0),
        ("frontend", 1.0),
        ("backend", 1.0),
        ("database", 1.0),
        ("query", 0.8),
        ("regex", 1.2),
        ("pattern", 0.8),
        ("sort", 0.8),
        ("filter", 0.8),
        ("map", 0.5),
        ("reduce", 0.8),
        ("loop", 0.8),
        ("recursion", 1.2),
        ("stack", 0.8),
        ("queue", 0.8),
        ("tree", 0.5),
        ("graph", 0.5),
        ("hash", 0.8),
        ("array", 0.8),
        ("list", 0.5),
        ("string", 0.5),
        ("integer", 0.5),
        ("boolean", 0.5),
        ("null", 0.5),
        ("undefined", 0.5),
        ("error", 0.5),
        ("exception", 0.8),
        ("try", 0.5),
        ("catch", 0.5),
        ("finally", 0.5),
        ("throw", 0.5),
        ("promise", 0.8),
        ("async", 1.0),
        ("await", 1.0),
        ("callback", 1.0),
        ("closure", 1.0),
        ("prototype", 0.8),
        ("inheritance", 1.0),
        ("polymorphism", 1.0),
        ("abstraction", 1.0),
        ("encapsulation", 1.0),
        ("interface", 0.8),
        ("abstract", 0.8),
        ("virtual", 0.8),
        ("static", 0.5),
        ("dynamic", 0.5),
        ("strongly typed", 1.0),
        ("weakly typed", 1.0),
        ("type inference", 1.0),
        ("generic", 0.8),
        ("template", 0.8),
        ("macro", 0.8),
        (" decorator", 1.0),
        ("annotation", 0.8),
        ("attribute", 0.8),
        ("property", 0.8),
        ("method", 0.8),
        ("constructor", 1.0),
        ("destructor", 1.0),
        ("factory", 1.0),
        ("singleton", 1.0),
        ("observer", 1.0),
        ("strategy", 1.0),
        ("adapter", 1.0),
        ("proxy", 1.0),
        ("mediator", 1.0),
        ("memento", 1.0),
        ("command", 0.8),
        ("iterator", 1.0),
        ("visitor", 1.0),
        ("state", 0.5),
        ("chain of responsibility", 1.5),
        ("composite", 1.0),
        ("bridge", 1.0),
        ("flyweight", 1.0),
    ],
    "RES": [
        ("news", 1.5),
        ("headline", 1.5),
        ("happening", 1.0),
        ("world", 0.8),
        ("update", 0.8),
        ("today", 0.8),
        ("latest", 0.8),
        ("current events", 1.5),
        ("breaking", 1.5),
        ("search", 1.0),
        ("find", 0.8),
        ("research", 1.2),
        ("lookup", 1.0),
        ("google", 1.0),
        ("web search", 1.5),
        ("khabar", 1.5),
        ("samachar", 1.5),
        ("what is", 0.8),
        ("who is", 0.8),
        ("when was", 0.8),
        ("where is", 0.8),
        ("how many", 0.8),
        ("tell me about", 1.0),
        ("info about", 1.0),
        ("compare", 0.8),
        ("difference between", 1.0),
        ("review", 0.8),
        ("analysis", 0.8),
    ],
    "WRI": [
        ("write", 1.0),
        ("article", 1.2),
        ("blog", 1.5),
        ("post", 0.8),
        ("essay", 1.5),
        ("story", 1.2),
        ("poem", 1.5),
        ("haiku", 2.0),
        ("content", 0.8),
        ("copywriting", 1.5),
        ("draft", 1.2),
        ("letter", 1.2),
        ("email draft", 1.5),
        ("proposal", 1.2),
        ("summary", 1.0),
        ("summarize", 1.5),
        ("rewrite", 1.2),
        ("paraphrase", 1.2),
        ("creative writing", 2.0),
        ("fiction", 1.5),
        ("narrative", 1.2),
        ("script", 0.8),
        ("caption", 1.0),
        ("description", 0.8),
        ("likh", 1.5),
        ("likho", 1.5),
        ("likh de", 1.5),
        ("translate", 1.5),
        ("translation", 1.5),
        ("language", 0.8),
    ],
    "COA": [
        ("coordinate", 1.5),
        ("workflow", 1.2),
        ("pipeline", 1.2),
        ("automate", 1.2),
        ("n8n", 2.0),
        ("zapier", 1.5),
        ("integrate", 1.0),
        ("connect", 0.8),
        ("sync", 0.8),
        ("schedule task", 1.5),
        ("cron", 1.5),
        ("trigger", 1.0),
        ("webhook", 1.2),
        ("chain", 0.8),
        ("sequence", 1.0),
    ],
    "DEC": [
        ("decide", 1.5),
        ("decision", 1.5),
        ("analyze", 1.0),
        ("pros and cons", 2.0),
        ("compare", 0.8),
        ("evaluate", 1.2),
        ("recommend", 1.2),
        ("should I", 1.5),
        ("which is better", 2.0),
        ("trade-off", 1.5),
        ("tradeoff", 1.5),
        ("weigh", 1.0),
        ("option", 0.8),
        ("alternative", 1.0),
        ("choice", 0.8),
    ],
    "SEC": [
        ("security", 1.5),
        ("scan", 1.0),
        ("port scan", 2.0),
        ("firewall", 1.5),
        ("threat", 1.5),
        ("vulnerability", 1.5),
        ("hack", 1.0),
        ("protect", 1.0),
        ("encrypt", 1.2),
        ("password", 1.0),
        ("auth", 0.8),
        ("permission", 0.8),
        ("audit", 1.2),
        ("risk", 1.0),
        ("attack", 1.0),
        ("intrusion", 1.5),
        ("malware", 1.5),
        ("virus", 1.2),
    ],
    "MEM": [
        ("remember", 1.5),
        ("recall", 1.5),
        ("memory", 1.0),
        ("forget", 1.0),
        ("stored", 0.8),
        ("saved", 0.8),
        ("what did I", 1.2),
        ("earlier", 0.8),
        ("before", 0.8),
        ("past", 0.8),
        ("history", 0.8),
        ("previously", 1.0),
        ("yaad", 1.5),
        ("yaad hai", 2.0),
        ("bhul", 1.0),
    ],
    "SCR": [
        ("screenshot", 1.5),
        ("screen", 0.8),
        ("capture", 1.0),
        ("ocr", 2.0),
        ("read screen", 2.0),
        ("what's on screen", 2.0),
        ("visual", 0.8),
        ("image analysis", 1.5),
        ("describe screen", 2.0),
        ("element", 0.8),
        ("button", 0.8),
        ("ui", 0.8),
    ],
    "SOC": [
        ("social media", 2.0),
        ("twitter", 1.5),
        ("instagram", 1.5),
        ("linkedin", 1.5),
        ("facebook", 1.5),
        ("tiktok", 1.5),
        ("youtube", 1.2),
        ("post tweet", 2.0),
        ("tweet", 1.5),
        ("social", 1.0),
        ("share", 0.8),
        ("publish", 1.0),
    ],
    "MTG": [
        ("meeting", 2.0),
        ("schedule meeting", 2.0),
        ("calendar", 1.0),
        ("zoom", 1.5),
        ("teams", 1.0),
        ("call", 0.8),
        ("attendees", 1.5),
        ("agenda", 1.5),
        ("minutes", 1.2),
        ("invite", 0.8),
        ("join meeting", 2.0),
    ],
    "FTN": [
        ("fine-tune", 1.5),
        ("finetune", 1.5),
        ("train model", 2.0),
        ("fine tune", 1.5),
        ("model training", 2.0),
        ("dataset", 1.2),
        ("epochs", 1.5),
        ("loss", 1.0),
        ("accuracy", 0.8),
        ("huggingface", 1.0),
        ("lora", 1.5),
        ("qlora", 1.5),
    ],
    "THB": [
        ("thumbnail", 2.0),
        ("youtube thumbnail", 2.0),
        ("cover image", 1.5),
        ("banner", 1.2),
        ("icon design", 1.5),
        ("graphic", 1.0),
        ("design", 0.8),
        ("visual design", 1.2),
    ],
    "SEO": [
        ("seo", 2.0),
        ("search engine optimization", 2.0),
        ("meta tag", 1.5),
        ("keyword", 1.0),
        ("backlink", 1.5),
        ("ranking", 1.0),
        ("serp", 1.5),
        ("organic", 0.8),
        ("seo audit", 2.0),
        ("on-page", 1.2),
        ("off-page", 1.2),
    ],
    "QNT": [
        ("quarantine", 2.0),
        ("isolate", 1.5),
        ("suspicious file", 2.0),
        ("infected", 1.5),
        ("malware file", 2.0),
        ("contain", 1.0),
        ("containment", 1.5),
    ],
    "LOG": [
        ("log", 1.0),
        ("logs", 1.0),
        ("analyze log", 2.0),
        ("error log", 2.0),
        ("server log", 2.0),
        ("crash log", 2.0),
        ("debug log", 2.0),
        ("log analysis", 2.0),
        ("syslog", 1.5),
    ],
    "CNC": [
        ("council", 1.5),
        ("debate", 1.0),
        ("multiple perspective", 1.5),
        ("brainstorm", 1.2),
        ("panel", 0.8),
        ("committee", 1.0),
        ("deliberate", 1.5),
        ("consensus", 1.5),
    ],
    "PLN": [
        ("plan", 1.0),
        ("planning", 1.2),
        ("roadmap", 1.5),
        ("breakdown", 1.0),
        ("decompose", 1.5),
        ("task list", 1.5),
        ("steps", 0.8),
        ("phases", 1.0),
        ("milestone", 1.2),
        ("project plan", 2.0),
        ("action plan", 1.5),
    ],
    "EXE": [
        ("execute", 1.0),
        ("run task", 1.5),
        ("perform", 0.8),
        ("carry out", 1.0),
        ("do this", 0.8),
        ("handle this", 1.0),
        ("complete this", 1.2),
        ("finish this", 1.2),
    ],
    "CRT": [
        ("review", 1.0),
        ("critique", 1.5),
        ("evaluate", 1.0),
        ("quality", 0.8),
        ("check code", 1.5),
        ("code review", 2.0),
        ("improve", 0.8),
        ("feedback", 1.0),
        ("suggestion", 0.8),
    ],
    "PMA": [
        ("pipeline memory", 2.0),
        ("context", 0.8),
        ("pipeline context", 2.0),
        ("prior results", 1.5),
        ("previous task", 1.0),
    ],
    "HRM": [
        ("deep reason", 2.0),
        ("think step by step", 1.5),
        ("chain of thought", 2.0),
        ("reasoning", 1.0),
        ("complex problem", 1.5),
        ("logic", 1.0),
        ("philosophy", 1.2),
        ("explain why", 1.0),
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
#  FEATURE KEYWORD MAP — queries → auto-open panels
# ═══════════════════════════════════════════════════════════════════════════════

FEATURE_KEYWORDS: Dict[str, List[Tuple[str, float]]] = {
    "dashboard": [
        ("dashboard", 2.0),
        ("status", 0.8),
        ("overview", 1.0),
        ("system status", 1.5),
        ("health", 0.8),
    ],
    "settings": [
        ("settings", 2.0),
        ("config", 1.5),
        ("configuration", 1.5),
        ("preferences", 1.5),
        ("setup", 0.8),
        ("api key", 1.5),
    ],
    "goals": [
        ("goal", 1.5),
        ("goals", 1.5),
        ("target", 1.0),
        ("objective", 1.2),
        ("milestone", 1.0),
        ("track goal", 2.0),
    ],
    "habits": [
        ("habit", 1.5),
        ("habits", 1.5),
        ("routine", 1.2),
        ("daily routine", 2.0),
        ("track habit", 2.0),
        ("streak", 1.0),
    ],
    "mood": [
        ("mood", 1.5),
        ("how am I feeling", 2.0),
        ("emotion", 1.0),
        ("mood tracker", 2.0),
        ("feeling", 0.8),
    ],
    "finance": [
        ("finance", 1.5),
        ("money", 1.0),
        ("budget", 1.5),
        ("expense", 1.2),
        ("income", 1.2),
        ("spending", 1.0),
        ("investment", 1.0),
        ("profit", 0.8),
        ("loss", 0.8),
    ],
    "ideas": [
        ("idea", 1.2),
        ("ideas", 1.2),
        ("brainstorm", 1.0),
        ("idea vault", 2.0),
        ("idea bank", 2.0),
        ("innovation", 1.0),
    ],
    "relationships": [
        ("relationship", 1.5),
        ("contact", 0.8),
        ("person", 0.8),
        ("friend", 0.8),
        ("family", 0.8),
        ("colleague", 0.8),
        ("birthday", 1.0),
        ("anniversary", 1.0),
    ],
    "reasoning": [
        ("reason", 1.0),
        ("think", 0.8),
        ("logic", 1.0),
        ("reasoning", 1.5),
        ("explain reasoning", 2.0),
    ],
    "decisions": [
        ("decision", 1.5),
        ("decide", 1.2),
        ("choice", 0.8),
        ("which should", 1.5),
        ("pros cons", 2.0),
    ],
    "analytics": [
        ("analytics", 2.0),
        ("metrics", 1.0),
        ("data analysis", 1.5),
        ("statistics", 1.2),
        ("insights", 1.0),
    ],
    "knowledge": [
        ("knowledge", 1.5),
        ("learn", 0.8),
        ("study", 0.8),
        ("knowledge base", 2.0),
        ("wiki", 1.0),
        ("reference", 0.8),
    ],
    "memory": [
        ("memory", 1.0),
        ("remember", 1.2),
        ("recall", 1.2),
        ("memory store", 2.0),
        ("memories", 1.0),
    ],
    "search": [
        ("search", 1.0),
        ("find", 0.8),
        ("universal search", 2.0),
        ("lookup", 0.8),
    ],
    "conversation": [
        ("conversation", 1.5),
        ("chat history", 1.5),
        ("past chat", 1.5),
        ("previous conversation", 2.0),
    ],
    "voice": [
        ("voice", 1.0),
        ("tts", 1.5),
        ("text to speech", 2.0),
        ("speech", 1.0),
        ("speak", 0.8),
        ("audio", 0.8),
    ],
    "gestures": [
        ("gesture", 1.5),
        ("hand gesture", 2.0),
        ("webcam", 1.0),
        ("hand tracking", 2.0),
        ("hand control", 2.0),
    ],
    "email": [
        ("email", 1.5),
        ("mail", 1.0),
        ("send email", 2.0),
        ("inbox", 1.0),
        ("compose email", 2.0),
    ],
    "calendar": [
        ("calendar", 1.5),
        ("event", 0.8),
        ("schedule", 1.0),
        ("appointment", 1.2),
        ("reminder", 1.0),
    ],
    "security": [
        ("security", 1.0),
        ("security hardening", 2.0),
        ("firewall", 1.0),
        ("encrypt", 1.0),
    ],
    "plugins": [
        ("plugin", 1.5),
        ("plugins", 1.5),
        ("plugin store", 2.0),
        ("extension", 1.0),
        ("addon", 1.0),
    ],
    "skills": [
        ("skill", 1.2),
        ("skills", 1.2),
        ("skill marketplace", 2.0),
        ("learn skill", 1.5),
    ],
    "watchdog": [
        ("watchdog", 2.0),
        ("monitor", 1.0),
        ("system monitor", 1.5),
        ("health check", 1.5),
    ],
    "selftest": [
        ("self test", 2.0),
        ("selftest", 2.0),
        ("test system", 1.5),
        ("diagnostics", 1.5),
    ],
    "updater": [
        ("update", 0.8),
        ("updater", 1.5),
        ("auto update", 2.0),
        ("check update", 1.5),
        ("new version", 1.5),
    ],
    "bootreport": [
        ("boot", 1.0),
        ("boot report", 2.0),
        ("startup", 1.0),
        ("startup report", 2.0),
    ],
    "metrics": [
        ("honest metrics", 2.0),
        ("metrics", 1.0),
        ("real metrics", 1.5),
    ],
    "health": [
        ("system health", 2.0),
        ("health dashboard", 2.0),
        ("health status", 1.5),
    ],
    "version": [
        ("version", 1.5),
        ("app version", 1.5),
        ("orbit version", 2.0),
    ],
    "notifications": [
        ("notification", 1.5),
        ("notifications", 1.5),
        ("alerts", 1.0),
        ("notify", 1.0),
    ],
    "proactive": [
        ("proactive", 1.5),
        ("remind me", 1.5),
        ("reminder", 1.2),
        ("nudge", 1.0),
        ("proactive assistant", 2.0),
    ],
    "desktop": [
        ("desktop control", 2.0),
        ("desktop", 0.8),
        ("remote desktop", 2.0),
    ],
    "github": [
        ("github", 2.0),
        ("git", 1.0),
        ("repo", 1.0),
        ("repository", 1.2),
        ("commit", 1.0),
        ("pull request", 1.5),
    ],
    "multimodal": [
        ("multimodal", 2.0),
        ("image analysis", 1.5),
        ("vision", 1.0),
        ("analyze image", 2.0),
        ("describe image", 2.0),
    ],
    "n8n": [
        ("n8n", 2.0),
        ("workflow automation", 1.5),
        ("automation", 1.0),
    ],
    "techscout": [
        ("tech scout", 2.0),
        ("technology trends", 1.5),
        ("new tech", 1.2),
        ("tech news", 1.5),
    ],
    "briefing": [
        ("briefing", 2.0),
        ("daily briefing", 2.0),
        ("morning briefing", 2.0),
        ("summary briefing", 1.5),
    ],
    "overkill_smartbrowser": [
        ("smart browser", 2.0),
        ("browse", 1.0),
        ("web browser", 1.5),
        ("open website", 1.5),
        ("navigate to", 1.5),
    ],
    "overkill_skillmarket": [
        ("skill marketplace", 2.0),
        ("browse skills", 1.5),
    ],
    "mcp": [
        ("mcp", 2.0),
        ("model context protocol", 2.0),
    ],
    "overkill_anomaly": [
        ("anomaly", 1.5),
        ("anomaly detector", 2.0),
        ("unusual", 1.0),
        ("detect anomaly", 2.0),
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL COMMAND PATTERNS — direct tool execution
# ═══════════════════════════════════════════════════════════════════════════════

TOOL_PATTERNS = [
    (re.compile(r"^(?:open|launch|start|khol|kholo|run)\s+", re.I), "TAS", "computer"),
    (re.compile(r"\bclipboard\b|\bcopy\s+to\s+clipboard\b|\bpaste\b", re.I), "TAS", "computer"),
    (re.compile(r"\bscreenshot\b|\bscreen\s+capture\b", re.I), "SCR", "screenshot"),
    (re.compile(r"\bsearch\b.*(?:google|web|internet)", re.I), "RES", "websearch"),
    (re.compile(r"\bnews\b|\bheadlines?\b|\bkhabar\b", re.I), "RES", "news"),
    # ── Code generation: MUST precede the generic file-write pattern below
    #    ("Write a Python function ... save it to x.py" was stolen by "*.py").
    #    Also before the MEM memory pattern: "system information ... memory"
    #    used to match \bmemory\b first. Negative lookahead keeps
    #    "Create a Python file with ..." on the file path.
    (
        re.compile(
            r"\b(?:write|create|generate|make)\s+(?:a\s+|an\s+|some\s+|the\s+)?(?:new\s+)?"
            r"(?:python|javascript|typescript|java|c\+\+|golang|rust|sql|function|script|code|program|algorithm|regex)"
            r"(?:\s+(?:function|script|code|program|class))?"
            r"(?!file\s+(?:called|named|with))",
            re.I,
        ),
        "COD",
        "code",
    ),
    # ── System info: "Tell me the system information including OS and memory" ─
    (
        re.compile(
            r"\bsystem\s+(?:info|information|details|specs?)\b|\bos\s+and\s+memory\b|\b(?:machine|hardware)\s+info\b",
            re.I,
        ),
        "TAS",
        "computer",
    ),
    (re.compile(r"\bremember\b|\brecall\b|\bmemory\b", re.I), "MEM", "memory"),
    (re.compile(r"\bsecurity\s+scan\b|\bport\s+scan\b|\bthreat\b", re.I), "SEC", "security"),
    # ── File operations: write/create/move/rename/copy/delete ─────────────
    (
        re.compile(
            r"\bwrite\s+file\b|\bcreate\s+file\b|\bcreate\s+a\s+file\b|\bsave\s+(?:to\s+)?file\b|\badd\s+(?:text|content|line|item)\s+to\b"
            r"|\bappend\b|\badd\s+[`\"']"
            r"|\bcreate\s+\S+\s+(?:with|containing)\b|\bwrite\s+\S+\s+(?:with|containing)\b"
            r"|\bsave\s+(?:this|it|the)\s+(?:to|in|as)\b|\bwrite\s+(?:this|it|the)\s+(?:to|in|as)\b"
            r"|\bcreate\s+(?:a\s+)?(?:file|document|text)\b"
            r"|\b(?:file|document)\s+(?:called|named|titled|with\s+name)\b"
            r"|\b(?:write|create|save|make)\b.*\b(?:file|txt|py|json|md|csv|log)\b",
            re.I,
        ),
        "TAS",
        "file",
    ),
    (
        re.compile(
            r"\bread\s+file\b|\bread\s+[`\"']?[\w./\\-]+\.[a-z0-9]+\b|\bshow\s+file\b"
            r"|\b(?:list|show)\s+(?:the\s+)?(?:all\s+)?(?:\w+\s+){0,3}?files?\b"
            r"|\b(?:list|show)\s+(?:the\s+)?(?:all\s+)?(?:\w+\s+){0,3}?(?:directory|directories|folder)\b"
            r"|\bfile\s+info\b"
            r"|\bsearch\s+files?\b|\bsearch\s+(?:for\s+)?\S+\s+files?\b|\bfind\s+(?:the\s+)?files?\b"
            r"|\bcopy\s+file\b"
            r"|\bdelete\s+(?:the\s+)?(?:file|folder|directory)\b|\bdelete\s+[`\"']?[\w./\\-]+\.[a-z0-9]+\b"
            r"|\bread\s+pdf\b",
            re.I,
        ),
        "TAS",
        "file",
    ),
    # ── Move/Rename file ──────────────────────────────────────────────────
    (re.compile(r"\bmove\s+(?:file\s+)?\S+\s+(?:to|into)\s+\S+", re.I), "TAS", "file"),
    (re.compile(r"\brename\s+(?:file\s+)?\S+\s+(?:to|as)\s+\S+", re.I), "TAS", "file"),
    # ── Email ─────────────────────────────────────────────────────────────
    (
        re.compile(r"\bemail\b.*\bsend\b|\bsend\b.*\bemail\b|\bmail\b.*\bsend\b|\bsend\b.*\bmail\b", re.I),
        "WRI",
        "email",
    ),
    # ── Calendar / Meeting ────────────────────────────────────────────────
    (
        re.compile(r"\b(?:create|add|schedule)\s+(?:a\s+)?(?:calendar\s+)?(?:event|meeting|appointment)\b", re.I),
        "MTG",
        "calendar",
    ),
    (re.compile(r"\bmeeting\b.*\bschedule\b|\bschedule\b.*\bmeeting\b", re.I), "MTG", "calendar"),
    # ── IP address ────────────────────────────────────────────────────────
    # Word boundaries required: bare "ip" matches skip/clip/flip/script.
    (
        re.compile(r"\b(?:what(?:'s|\s+is)\s+)?(?:my\s+)?(?:ip|ip\s+address|local\s+ip|public\s+ip)\b", re.I),
        "TAS",
        "computer",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
#  TRAINING DATA — feedback-based adjustments
# ═══════════════════════════════════════════════════════════════════════════════

_TRAINING_FILE = None  # resolved lazily via runtime_paths (frozen-safe)


def _training_path() -> Path:
    global _TRAINING_FILE
    if _TRAINING_FILE is None:
        from .runtime_paths import orbit_data_dir

        _TRAINING_FILE = orbit_data_dir() / "smart_router_training.json"
    return _TRAINING_FILE
_training_lock = threading.Lock()
_training_data: Dict[str, Dict[str, float]] = {}  # agent_label -> {keyword: boost}


def _load_training():
    global _training_data
    try:
        path = _training_path()
        if path.exists():
            with _training_lock:
                _training_data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass


_last_training_save = 0.0
_TRAINING_SAVE_DEBOUNCE = 5.0  # seconds


def _save_training(force: bool = False):
    global _last_training_save
    try:
        now = time.time()
        if not force and (now - _last_training_save) < _TRAINING_SAVE_DEBOUNCE:
            return
        _last_training_save = now
        path = _training_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _training_lock:
            path.write_text(json.dumps(_training_data, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Failed to save training data")


def record_routing_feedback(agent_label: str, query: str, correct: bool):
    """Record feedback to improve future routing."""
    with _training_lock:
        if agent_label not in _training_data:
            _training_data[agent_label] = {}
        words = re.findall(r"\w+", query.lower())
        for word in words:
            if len(word) < 3:
                continue
            key = word
            current = _training_data[agent_label].get(key, 0.0)
            if correct:
                _training_data[agent_label][key] = current + 0.1
            else:
                _training_data[agent_label][key] = current - 0.15
    _save_training()


# ═══════════════════════════════════════════════════════════════════════════════
#  SMART ROUTER
# ═══════════════════════════════════════════════════════════════════════════════


class SmartRouter:
    """Deterministic auto-router with topic memory and follow-up detection."""

    def __init__(self):
        _load_training()
        self._lock = threading.RLock()
        self._stats = {"routes": 0, "agent_hits": 0, "feature_hits": 0}
        self._recent_topics: list[str] = []  # last 10 routed agent labels
        self._recent_intents: list[str] = []  # last 10 intents
        self._MAX_HISTORY = 10

    # ── topic memory ──────────────────────────────────────────────────────
    def _push_topic(self, agent_label: str, intent: str):
        with self._lock:
            self._recent_topics.append(agent_label)
            self._recent_intents.append(intent)
            if len(self._recent_topics) > self._MAX_HISTORY:
                self._recent_topics = self._recent_topics[-self._MAX_HISTORY :]
                self._recent_intents = self._recent_intents[-self._MAX_HISTORY :]

    def _is_followup(self, text: str) -> bool:
        return bool(
            re.match(
                r"^(and |also |additionally |what about |how about |tell me more |"
                r"same topic|continue|phir |aur |uske baad )",
                text.lower().strip(),
            )
        )

    def _has_pronoun(self, text: str) -> bool:
        return bool(
            re.match(
                r"^(it |its |that |this |them |those |their )",
                text.lower().strip(),
            )
        )

    def _topic_boost(self, result: RouteResult):
        """Boost the top agent when the query is a follow-up or pronoun reference."""
        if not result.agents:
            return
        with self._lock:
            recent = list(self._recent_topics)
        top = result.agents[0]
        # follow-up phrases: if last topic exists, boost matching agent
        if recent:
            last_topic = recent[-1]
            if top.label == last_topic:
                top = AgentMatch(
                    top.label,
                    min(top.score + 0.15, 1.0),
                    top.reason + " +topic_followup",
                )
                result.agents[0] = top

    def _learned_pref_boost(self, result: RouteResult, query: str):
        """Boost agent if SmartMemory has learned user prefers it for this topic.
        Also penalize agents with bad success rates."""
        if not result.agents:
            return
        try:
            from .smart_memory import get_smart_memory

            mem = get_smart_memory()
            topic = mem._extract_topic(query)
            if not topic:
                return

            # Penalize worst agent
            worst = mem.get_worst_agent_for_topic(topic)
            if worst:
                for i, agent in enumerate(result.agents):
                    if agent.label == worst:
                        new_score = max(agent.score - 0.3, 0.0)
                        result.agents[i] = AgentMatch(
                            agent.label,
                            new_score,
                            agent.reason + " -low_success",
                        )
                        break

            # Boost preferred agent
            preferred = mem.get_best_agent_for_topic(topic)
            if not preferred:
                return
            # Boost the preferred agent
            for i, agent in enumerate(result.agents):
                if agent.label == preferred:
                    result.agents[i] = AgentMatch(
                        agent.label,
                        min(agent.score + 0.2, 1.0),
                        agent.reason + " +learned_pref",
                    )
                    # Move to top if not already
                    if i > 0:
                        result.agents.insert(0, result.agents.pop(i))
                    break
            else:
                # Preferred agent not in results — add it with moderate score
                result.agents.append(AgentMatch(preferred, 0.4, "learned_pref"))
        except Exception:
            pass  # Never break routing on learning failure

    def route(self, text: str, history: Optional[List[str]] = None) -> RouteResult:
        """Analyze text and return best agent + feature matches.

        Args:
            text: User message to route
            history: Optional list of recent user messages for context-aware routing
        """
        """Analyze text and return best agent + feature matches."""
        with self._lock:
            self._stats["routes"] += 1
        text_lower = text.lower().strip()
        result = RouteResult()

        if not text_lower:
            return result

        # Step 1: Check tool command patterns (highest priority)
        for pattern, agent_label, intent in TOOL_PATTERNS:
            if pattern.search(text_lower):
                result.is_tool_command = True
                result.intent = intent
                if agent_label:
                    result.agents.append(AgentMatch(agent_label, 1.0, "tool_pattern"))
                break

        # Step 2: Score agents
        if not result.agents:
            agent_scores = self._score_agents(text_lower)
            for label, score, reason in agent_scores:
                if score > 0.3:
                    result.agents.append(AgentMatch(label, score, reason))
            result.agents.sort(key=lambda a: a.score, reverse=True)

        # Step 2.5: Context window — boost agents that match recent conversation
        if history and result.agents:
            recent_agents = []
            for msg in history[-3:]:
                r = self._score_agents(msg.lower().strip())
                if r:
                    recent_agents.append(r[0][0])  # top agent label from each recent message
            if recent_agents:
                top_label = result.agents[0].label
                count = recent_agents.count(top_label)
                if count >= 2:
                    # Agent was dominant in recent context — boost by up to 15%
                    result.agents[0] = AgentMatch(
                        top_label,
                        min(result.agents[0].score + 0.05 * count, 1.0),
                        result.agents[0].reason + " +context",
                    )

        # Step 2.6: Topic memory — boost follow-ups and pronoun references
        if result.agents:
            self._topic_boost(result)

        # Step 2.7: Learned preferences — boost agent user prefers for this topic
        if result.agents:
            self._learned_pref_boost(result, text_lower)

        # Step 2.8: Agent confidence — boost/penalize based on historical success
        if result.agents:
            self._agent_confidence_boost(result, text_lower)

        # Step 3: Score features
        feature_scores = self._score_features(text_lower)
        for panel, cmd, score, reason in feature_scores:
            if score > 0.3:
                result.features.append(FeatureMatch(panel, cmd, score, reason))
        result.features.sort(key=lambda f: f.score, reverse=True)

        # Step 4: Determine intent
        if not result.intent:
            result.intent = self._classify_intent(text_lower)

        # Step 5: Overall confidence + routing decision
        if result.agents:
            result.confidence = result.agents[0].score
            result.negated = "negated" in result.agents[0].reason
            with self._lock:
                self._stats["agent_hits"] += 1
            # Confidence-calibrated routing
            if result.agents[0].score >= 0.7:
                result.routing = "direct_agent"
            elif result.agents[0].score >= 0.35:
                result.routing = "agent_with_llm"
            else:
                result.routing = "llm_only"
        if result.features:
            with self._lock:
                self._stats["feature_hits"] += 1

        # Record topic for future follow-up detection
        if result.agents:
            self._push_topic(result.agents[0].label, result.intent or "general")

        return result

    def _expand_query(self, text: str) -> str:
        """Expand query with synonyms for better keyword matching."""
        words = text.lower().split()
        expanded = set(words)
        for word in words:
            if word in SYNONYM_MAP:
                expanded.update(SYNONYM_MAP[word])
        return " ".join(expanded)

    def _agent_confidence_boost(self, result: RouteResult, query: str):
        """Boost/penalize agents based on historical success rates."""
        if not result.agents:
            return
        try:
            from .smart_memory import get_smart_memory

            mem = get_smart_memory()
            topic = mem._extract_topic(query)
            if not topic:
                return

            for i, agent_match in enumerate(result.agents):
                rate = mem.get_agent_success_rate(agent_match.label, topic)
                # Boost if high success rate, penalize if low
                if rate > 0.7:
                    boost = min(0.15, (rate - 0.5) * 0.3)
                    result.agents[i] = AgentMatch(
                        agent_match.label,
                        min(agent_match.score + boost, 1.0),
                        agent_match.reason + f" +conf({rate:.0%})",
                    )
                elif rate < 0.4:
                    penalty = min(0.2, (0.5 - rate) * 0.4)
                    result.agents[i] = AgentMatch(
                        agent_match.label,
                        max(agent_match.score - penalty, 0.0),
                        agent_match.reason + f" -conf({rate:.0%})",
                    )
        except Exception:
            pass

    def _score_agents(self, text: str) -> List[Tuple[str, float, str]]:
        """Score all agents against the text with improved matching."""
        scores = []
        # Expand query with synonyms for better matching
        expanded_text = self._expand_query(text)
        words = set(re.findall(r"\w+", expanded_text))
        word_count = max(len(expanded_text.split()), 1)
        # Short queries get full weight, long queries get dampened to prevent score inflation
        density_factor = min(1.0, 8.0 / word_count)

        for label, keywords in AGENT_KEYWORDS.items():
            total = 0.0
            matched = []
            negated = False
            for phrase, weight in keywords:
                if " " not in phrase:
                    # Single-word: use word-boundary regex to avoid false positives
                    # e.g. "open" won't match "reopen" or "isOpen"
                    m = re.search(r"\b" + re.escape(phrase) + r"\b", text, re.I)
                    if m:
                        total += weight
                        matched.append(phrase)
                        if _has_negation(text, m.start()):
                            negated = True
                else:
                    # Multi-word: substring match (phrase boundaries are natural)
                    pos = text.find(phrase)
                    if pos >= 0:
                        total += weight
                        matched.append(phrase)
                        if _has_negation(text, pos):
                            negated = True

            # Apply training boosts
            with _training_lock:
                boosts = _training_data.get(label, {})
                for word in words:
                    if word in boosts:
                        total += boosts[word]

            # Apply density factor and log scaling (diminishing returns)
            total *= density_factor
            if total > 0:
                # Log scaling: preserves relative magnitude, never exceeds 1.0
                normalized = 1.0 - math.exp(-total / 2.0)
                reason = f"matched: {', '.join(matched[:3])}" if matched else "word_match"
                if negated:
                    normalized *= 0.3  # Heavy penalty for negated keywords
                    reason = f"negated: {reason}"
                scores.append((label, normalized, reason))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:3]

    def _score_features(self, text: str) -> List[Tuple[str, str, float, str]]:
        """Score all features against the text."""
        scores = []

        for command, keywords in FEATURE_KEYWORDS.items():
            total = 0.0
            matched = []
            for phrase, weight in keywords:
                if phrase in text:
                    total += weight
                    matched.append(phrase)

            if total > 0:
                panel = command.replace("overkill_", "").replace("_", " ").title()
                reason = f"matched: {', '.join(matched[:2])}"
                scores.append((panel, command, min(total / 2.5, 1.0), reason))

        scores.sort(key=lambda x: x[2], reverse=True)
        return scores[:3]

    def _classify_intent(self, text: str) -> str:
        """Classify the overall intent with weighted scoring."""
        intent_keywords = {
            "computer": [
                ("open", 1.5),
                ("launch", 1.5),
                ("run", 1.2),
                ("execute", 1.2),
                ("file", 1.0),
                ("folder", 1.0),
                ("process", 1.0),
                ("screenshot", 1.5),
                ("clipboard", 1.5),
                ("volume", 1.0),
                ("brightness", 1.0),
            ],
            "websearch": [
                ("search", 2.0),
                ("google", 2.0),
                ("find", 1.5),
                ("lookup", 1.5),
                ("web", 1.0),
                ("browse", 1.5),
                ("website", 1.5),
            ],
            "news": [
                ("news", 2.0),
                ("headline", 2.0),
                ("happening", 1.5),
                ("current events", 2.0),
                ("today", 1.0),
            ],
            "memory": [
                ("remember", 2.0),
                ("recall", 2.0),
                ("memory", 1.5),
                ("forget", 1.5),
                ("stored", 1.0),
            ],
            "security": [
                ("security", 2.0),
                ("scan", 1.5),
                ("threat", 2.0),
                ("firewall", 2.0),
                ("protect", 1.5),
                ("safe", 1.0),
            ],
            "email": [
                ("email", 2.0),
                ("send mail", 2.0),
                ("compose", 1.5),
                ("inbox", 1.5),
                ("message", 1.0),
            ],
            "calendar": [
                ("meeting", 2.0),
                ("schedule", 2.0),
                ("event", 1.5),
                ("appointment", 2.0),
                ("reminder", 1.5),
                ("deadline", 1.5),
            ],
            "code": [
                ("code", 2.0),
                ("program", 1.5),
                ("function", 1.5),
                ("debug", 2.0),
                ("refactor", 2.0),
                ("compile", 1.5),
                ("python", 1.5),
                ("javascript", 1.5),
                ("api", 1.0),
            ],
            "writing": [
                ("write", 2.0),
                ("article", 2.0),
                ("blog", 2.0),
                ("story", 1.5),
                ("poem", 2.0),
                ("draft", 1.5),
                ("compose", 1.5),
                ("author", 1.5),
            ],
            "research": [
                ("research", 2.0),
                ("analyze", 2.0),
                ("study", 1.5),
                ("investigate", 2.0),
                ("explore", 1.5),
                ("examine", 1.5),
            ],
            "chat": [
                ("hello", 1.0),
                ("hi", 0.8),
                ("hey", 0.8),
                ("how are you", 1.0),
                ("thanks", 1.0),
                ("bye", 1.0),
            ],
        }

        scores: Dict[str, float] = {}
        for intent, keywords in intent_keywords.items():
            total = 0.0
            for kw, weight in keywords:
                if kw in text:
                    total += weight
            if total > 0:
                scores[intent] = total

        if not scores:
            return "chat"

        # Return intent with highest score
        return max(scores, key=scores.get)

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self._stats)


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_router = None
_lock = threading.Lock()


def get_smart_router() -> SmartRouter:
    global _router
    if _router is None:
        with _lock:
            if _router is None:
                _router = SmartRouter()
    return _router


def auto_route(text: str) -> RouteResult:
    """Convenience function — route text and return result."""
    return get_smart_router().route(text)
