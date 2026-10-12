# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Core Assistant Module

This module implements the central Assistant class that orchestrates all
user interactions:

Key Responsibilities:
- Intent detection via keyword matching (35+ intents)
- Tool routing to appropriate handlers
- LLM communication via router.py
- Voice I/O coordination (STT/TTS)
- Conversation memory management
- Multi-agent delegation for complex tasks

Main Class:
    Assistant - Central orchestrator for all user interactions

Key Methods:
    send_text(text) - Process text input
    listen_and_respond() - Process voice input
    _detect_intent(text) - Keyword-based intent detection
    _handle_tool(intent, command) - Route to tool handler

Usage:
    from orbit.assistant import Assistant

    assistant = Assistant()
    response = assistant.send_text("What's the weather?")
"""

import os
import threading
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from . import conv_search as conv_module
from . import (
    emotions,
    proactive,
    stt,
    tts,
    tunnel,
    usage_tracker,
    wake_word as wake_word_module,
)
from . import memory as memory_module
from .core.llm import legacy_adapter as llm_router
from . import meeting as meeting_module
from .agents import AGENT_REGISTRY, QueenAgent
from .config import (
    DEFAULT_OLLAMA_MODEL,
    GF_NAME,
    GIRLFRIEND_SYSTEM_PROMPT,
    KOKORO_VOICE,
    TTS_ENGINE,
    TTS_SPEED,
    TTS_VOICE,
)
from .emotions import agent_label_for_intent, detect_sentiment
from .tools import ComputerTool, N8nTool, NewsTool, SecurityTool

from .logging_config import get_logger

logger = get_logger(__name__)

try:
    from .tools.cai_security import run_analysis as cai_analyze
    from .tools.cai_security import run_audit as cai_audit
    from .tools.cai_security import run_threat_analysis as cai_threat
except ImportError:

    def cai_analyze(_):
        return "CAI module unavailable"

    def cai_audit():
        return "CAI module unavailable"

    def cai_threat():
        return "CAI module unavailable"


TOOL_DEFINITIONS = {
    "computer": {
        "keywords": [
            "computer",
            "command",
            "run",
            "execute",
            "open",
            "launch",
            "process",
            "processes",
            "list processes",
            "running processes",
            "task",
            "tasks",
            "file",
            "folder",
            "directory",
            "clipboard",
            "copy",
            "paste",
            "copyboard",
            "kya hai clipboard",
            "system",
            "specs",
            "specification",
            "hardware",
        ],
        "weight": 1.0,
    },
    "news": {
        "keywords": [
            "news",
            "headline",
            "happening",
            "world",
            "update",
            "today",
            "latest",
            "current",
            "geopolitical",
            "intelligence",
            "global",
            "khabar",
            "samachar",
        ],
        "weight": 1.0,
    },
    "security": {
        "keywords": [
            "security",
            "scan",
            "virus",
            "threat",
            "port",
            "network",
            "disk",
            "system",
            "firewall",
            "defender",
            "kill",
            "service",
            "connection",
            "malware",
        ],
        "weight": 1.0,
    },
    "worldmonitor": {
        "keywords": ["worldmonitor", "world monitor", "monitor app", "global dashboard"],
        "weight": 1.5,
    },
    "n8n": {
        "keywords": ["n8n", "workflow", "automation", "webhook", "trigger", "activate", "workflow status"],
        "weight": 1.0,
    },
    "memory": {
        "keywords": [
            "remember",
            "memory",
            "recall",
            "forget",
            "clear",
            "previous",
            "conversation",
            "history",
            "yaad",
            "bhool",
        ],
        "weight": 1.0,
    },
    "cai": {
        "keywords": [
            "cai",
            "cyber security ai",
            "security analysis",
            "security audit",
            "threat analysis",
            "vulnerability",
            "cai audit",
            "cai threat",
        ],
        "weight": 1.2,
    },
    "screen": {
        "keywords": [
            "screen",
            "screenshot",
            "dekho",
            "dikhao",
            "what is on screen",
            "screen pe",
            "monitor",
            "display",
            "ocr",
            "read screen",
            "capture screen",
        ],
        "weight": 1.0,
    },
    "screen_share": {
        "keywords": [
            "share screen",
            "screen share",
            "start sharing",
            "stop sharing",
            "screen broadcast",
            "live screen",
            "stream screen",
            "screen streaming",
        ],
        "weight": 1.5,
    },
    "dictation": {
        "keywords": ["dictate", "dictation", "type", "voice type", "speech to text", "dikteishan"],
        "weight": 1.0,
    },
    "council": {
        "keywords": ["council", "advisors", "advisory", "काउंसिल"],
        "weight": 2.0,
    },
    "schedule": {
        "keywords": [
            "schedule",
            "task",
            "remind",
            "reminder",
            "todo",
            "plan",
            "remainder",
            "shedual",
            "remeber me",
            "yaad dilana",
            "task list",
            "show tasks",
            "pending",
            "overdue",
        ],
        "weight": 1.2,
    },
    "pipeline": {
        "keywords": [
            "handle this task",
            "pipeline",
            "multi step",
            "research and",
            "research and summarize",
            "analyze and",
            "find and summarize",
            "search and",
        ],
        "weight": 2.0,
    },
    "notes": {
        "keywords": [
            "note",
            "notes",
            "remember this",
            "write this down",
            "note down",
            "save this",
            "note kar",
            "likh lo",
            "show notes",
            "my notes",
            "meri notes",
            "search note",
            "search notes",
            "dhund note",
            "find note",
        ],
        "weight": 1.0,
    },
    "focus": {
        "keywords": [
            "focus",
            "focus mode",
            "pomodoro",
            "start focus",
            "stop focus",
            "focus time",
            "focus karo",
            "time left",
            "kitna bacha",
        ],
        "weight": 1.5,
    },
    "clipboard": {
        "keywords": ["clipboard", "copy", "paste", "clipboard history", "copy kiya", "last copy", "clipboard dikha"],
        "weight": 1.2,
    },
    "bookmark": {
        "keywords": [
            "bookmark",
            "bookmarks",
            "save link",
            "save url",
            "bookmark kar",
            "bookmark karo",
            "show bookmarks",
            "meri bookmarks",
            "bookmark dikha",
            "search bookmark",
        ],
        "weight": 1.0,
    },
    "calculate": {
        "keywords": [
            "calculate",
            "calculator",
            "what is",
            "solve",
            "compute",
            "convert",
            "conversion",
            "percent",
            "percentage",
            "math",
            "ganit",
            "hisab",
            "kitna hoga",
            "mul karo",
            "bhag karo",
            "jod",
            "ghat",
        ],
        "weight": 1.5,
    },
    "search": {
        "keywords": [
            "search conversation",
            "search chat",
            "find in chat",
            "search history",
            "conversation search",
            "baat search",
            "pehle kya bola",
            "purani baat",
            "dhund",
            "search karo",
        ],
        "weight": 1.3,
    },
    "habit": {
        "keywords": [
            "habit",
            "habits",
            "add habit",
            "new habit",
            "log habit",
            "mark habit",
            "habit karo",
            "habit done",
            "show habits",
            "habit streak",
            "meri habits",
            "aadat",
            "aadatein",
        ],
        "weight": 1.2,
    },
    "random": {
        "keywords": [
            "coin",
            "flip",
            "toss",
            "dice",
            "roll",
            "random",
            "pick",
            "choose",
            "between",
            "lucky",
            "yes or no",
            "no or yes",
            "decide",
            "chuno",
            "sikka",
            "pasa",
        ],
        "weight": 1.5,
    },
    "text": {
        "keywords": [
            "word count",
            "count words",
            "characters",
            "base64",
            "encode",
            "decode",
            "hash",
            "md5",
            "sha256",
            "uppercase",
            "lowercase",
            "title case",
            "slugify",
            "extract urls",
            "text utils",
            "shabdon",
        ],
        "weight": 1.0,
    },
    "stopwatch": {
        "keywords": [
            "stopwatch",
            "timer start",
            "start stopwatch",
            "lap",
            "stop stopwatch",
            "reset stopwatch",
            "stopwatch status",
        ],
        "weight": 1.5,
    },
    "password": {
        "keywords": [
            "password",
            "generate password",
            "strong password",
            "password generator",
            "pin",
            "password strength",
            "random password",
            "pwd",
        ],
        "weight": 1.2,
    },
    "sysinfo": {
        "keywords": [
            "system info",
            "system information",
            "specs",
            "computer specs",
            "battery",
            "cpu",
            "processor",
            "ram",
            "memory",
            "disk",
            "storage",
            "hardware",
            "specifications",
            "puri jankari",
        ],
        "weight": 1.0,
    },
    "list": {
        "keywords": [
            "list",
            "shopping list",
            "grocery list",
            "todo list",
            "create list",
            "add to list",
            "show list",
            "check item",
            "remove from list",
            "delete list",
            "sabji",
            "samaan",
            "khareedaari",
        ],
        "weight": 1.2,
    },
    "countdown": {
        "keywords": [
            "countdown",
            "days until",
            "days to",
            "event",
            "birthday",
            "anniversary",
            "upcoming",
            "kitne din bache",
            "event add",
            "show events",
            "delete event",
        ],
        "weight": 1.0,
    },
    "json": {
        "keywords": [
            "json",
            "format json",
            "minify json",
            "validate json",
            "extract keys",
            "json keys",
            "pretty json",
            "beautify json",
            "compress json",
        ],
        "weight": 1.0,
    },
    "journal": {
        "keywords": [
            "journal",
            "diary",
            "entry",
            "today entry",
            "show journal",
            "journal entry",
            "search journal",
            "diary entry",
            "dairy",
            "kya kiya",
        ],
        "weight": 1.0,
    },
    "baseconv": {
        "keywords": [
            "convert",
            "binary",
            "hex",
            "hexadecimal",
            "octal",
            "decimal",
            "base",
            "base64",
            "number system",
            "to binary",
            "to hex",
            "to octal",
            "to decimal",
        ],
        "weight": 1.0,
    },
    "emoji": {
        "keywords": ["emoji", "search emoji", "find emoji", "emoji search", "emojis"],
        "weight": 1.0,
    },
    "flashcard": {
        "keywords": [
            "flashcard",
            "cards",
            "study",
            "card add",
            "add card",
            "show cards",
            "study cards",
            "review card",
            "flip card",
            "next card",
            "card correct",
            "card wrong",
            "flash card",
        ],
        "weight": 1.2,
    },
    "expense": {
        "keywords": [
            "kharch",
            "kharcha",
            "expense",
            "spent",
            "spend",
            "expenditure",
            "budget",
            "money spent",
            "aaj ka kharcha",
            "mahine ka kharcha",
            "kitna kharcha",
        ],
        "weight": 1.2,
    },
    "color": {
        "keywords": [
            "color",
            "colour",
            "convert color",
            "hex to rgb",
            "rgb to hex",
            "hsl",
            "color convert",
            "colour convert",
            "hex color",
            "colour picker",
        ],
        "weight": 1.0,
    },
    "music": {
        "keywords": [
            "play",
            "pause",
            "next track",
            "previous track",
            "skip",
            "volume",
            "music",
            "song",
            "gaana",
            "chala",
            "stop music",
            "mute",
            "media",
        ],
        "weight": 1.2,
    },
    "network": {
        "keywords": [
            "network",
            "ip",
            "ip address",
            "my ip",
            "local ip",
            "public ip",
            "hostname",
            "dns",
            "gateway",
            "internet",
            "wifi",
        ],
        "weight": 1.0,
    },
    "files": {
        "keywords": [
            "analyze",
            "scan directory",
            "largest files",
            "disk usage",
            "file analysis",
            "folder analysis",
            "directory scan",
            "desktop files",
            "download folder",
            "kitne files",
        ],
        "weight": 1.0,
    },
    "briefing": {
        "keywords": [
            "good morning",
            "briefing",
            "brief",
            "aaj ka din",
            "morning report",
            "daily report",
            "kya chal raha",
            "update",
            "status report",
            "overview",
        ],
        "weight": 1.0,
    },
    "timer": {
        "keywords": [
            "timer",
            "set timer",
            "countdown",
            "5 minutes",
            "10 seconds",
            "30 second",
            "1 minute",
            "timer stop",
            "cancel timer",
            "timer status",
            "kitna bacha",
            "time baki",
        ],
        "weight": 1.5,
    },
    "decision": {
        "keywords": [
            "should i",
            "pros and cons",
            "pros & cons",
            "decision",
            "pro /",
            "con /",
            "decide",
            "suggestion",
            "kya karun",
            "kya karein",
        ],
        "weight": 1.2,
    },
    "websearch": {
        "keywords": [
            "search",
            "find",
            "dhund",
            "open website",
            "open url",
            "google search",
            "youtube search",
            "go to",
            "browser",
            "website khol",
            "internet search",
            "search on",
            "open",
        ],
        "weight": 1.2,
    },
    "share": {
        "keywords": [
            "share",
            "broadcast",
            "send to phone",
            "forward",
            "bhej",
            "bhejo",
            "share with edith",
            "share on phone",
            "share message",
            "share history",
        ],
        "weight": 1.0,
    },
    "quote": {
        "keywords": [
            "quote",
            "quotes",
            "inspiration",
            "inspire",
            "suvichar",
            "motivation",
            "kuch bata",
            "wise words",
            "thought",
            "thought of the day",
        ],
        "weight": 1.0,
    },
    "meeting": {
        "keywords": [
            "meeting",
            "google meet",
            "zoom",
            "teams",
            "transcribe meeting",
            "summarize meeting",
            "meeting notes",
            "simulate meeting",
            "show meetings",
            "list meetings",
            "my meetings",
            "meeting history",
            "past meetings",
            "delete meeting",
            "meeting log",
            "meeting status",
            "record meeting",
            "join meeting",
            "search meeting",
            "export meeting",
            "export all meetings",
            "meeting action items",
            "meeting to notes",
            "save meeting to notes",
            "meeting stats",
            "schedule meeting",
            "scheduled meetings",
            "scheduled meeting",
            "upcoming meetings",
            "remind me about meeting",
            "my scheduled meetings",
            "meeting brief",
        ],
        "weight": 1.2,
    },
}

TOOL_CONTEXT_HINTS = {
    "computer": ["🖥️", "💻", "⚙️", "run", "execute", "open", "launch"],
    "news": ["📰", "🌍", "khabar", "headline", "happening"],
    "security": ["🔒", "🛡️", "scan", "virus", "threat", "firewall"],
    "n8n": ["⚡", "🤖", "automation", "workflow"],
    "memory": ["🧠", "💾", "remember", "recall", "forget"],
    "cai": ["🔐", "🧠", "audit", "threat", "vulnerability"],
    "screen": ["🖥️", "📸", "screenshot", "screen"],
    "screen_share": ["🖥️", "📡", "share", "broadcast", "live", "stream"],
    "dictation": ["🎤", "type", "dictate"],
    "worldmonitor": ["🌍", "globe", "world"],
    "schedule": ["📋", "📅", "⏰", "task", "remind", "todo", "plan"],
    "pipeline": ["🔧", "⚙️", "pipeline", "multi-step", "research"],
    "notes": ["📝", "📋", "note", "remember", "save"],
    "focus": ["⏱️", "🎯", "focus", "pomodoro", "timer"],
    "clipboard": ["📋", "clipboard", "copy", "paste"],
    "bookmark": ["🔖", "bookmark", "save link", "favorite"],
    "calculate": ["🧮", "calculate", "convert", "math", "percent"],
    "search": ["🔍", "search", "find", "dhund", "conversation"],
    "habit": ["✅", "habit", "aadat", "streak", "routine"],
    "random": ["🎲", "coin", "dice", "random", "pick", "lucky", "decide"],
    "text": ["📝", "text", "word", "base64", "hash", "encode", "decode", "slug"],
    "stopwatch": ["⏱", "stopwatch", "lap", "timer"],
    "password": ["🔑", "password", "pin", "secure"],
    "sysinfo": ["🖥️", "system", "specs", "cpu", "ram", "battery"],
    "list": ["📋", "list", "shopping", "grocery", "sabji"],
    "countdown": ["📅", "countdown", "days", "event", "birthday"],
    "json": ["📋", "json", "format", "validate", "minify"],
    "journal": ["📓", "journal", "diary", "entry"],
    "baseconv": ["🔢", "convert", "binary", "hex", "decimal"],
    "emoji": ["😊", "emoji", "search"],
    "flashcard": ["🃏", "flashcard", "study", "card"],
    "expense": ["💰", "kharcha", "expense", "budget"],
    "color": ["🎨", "color", "hex", "rgb", "hsl"],
    "music": ["🎵", "music", "play", "pause", "volume", "gaana"],
    "network": ["🌐", "network", "ip", "hostname", "dns"],
    "files": ["📁", "directory", "file", "analyze", "disk"],
    "briefing": ["📰", "morning", "briefing", "overview", "aaj"],
    "timer": ["⏰", "timer", "countdown", "alarm", "remind"],
    "decision": ["⚖️", "decision", "pros", "cons", "should"],
    "websearch": ["🔍", "search", "find", "open", "browser", "dhund"],
    "share": ["📤", "share", "broadcast", "send", "bhej"],
    "quote": ["💬", "quote", "inspiration", "suvichar", "motivation"],
    "meeting": ["🤝", "🎙️", "meeting", "meet", "zoom", "transcript", "notes", "summary"],
}


class Assistant:
    HISTORY_MAX_AGE_HOURS = 24

    def __init__(self):
        self.model = DEFAULT_OLLAMA_MODEL
        self.tts_voice = TTS_VOICE
        self.tts_speed = TTS_SPEED
        self.tts_engine = TTS_ENGINE
        self.kokoro_voice = os.getenv("KOKORO_VOICE", KOKORO_VOICE)
        tts.set_engine(self.tts_engine)
        self.mic_index = None
        self.history = []
        self._history_lock = threading.RLock()
        self.listening = False
        self._listen_lock = threading.Lock()
        self.always_listen = False
        self.wake_word_enabled = False
        self._wake_word_detector = None
        self.speak_responses = True
        self.girlfriend_mode = False
        self.offline_mode = False
        self.force_offline = False
        self.skill_counts = {
            "computer": 0,
            "news": 0,
            "security": 0,
            "n8n": 0,
            "worldmonitor": 0,
            "memory": 0,
            "cai": 0,
            "voice": 0,
            "chat": 0,
            "screen": 0,
            "screen_share": 0,
            "schedule": 0,
            "pipeline": 0,
            "notes": 0,
            "focus": 0,
            "clipboard": 0,
            "bookmark": 0,
            "calculate": 0,
            "search": 0,
            "habit": 0,
            "random": 0,
            "text": 0,
            "stopwatch": 0,
            "password": 0,
            "sysinfo": 0,
            "list": 0,
            "countdown": 0,
            "json": 0,
            "journal": 0,
            "baseconv": 0,
            "emoji": 0,
            "flashcard": 0,
            "expense": 0,
            "color": 0,
            "music": 0,
            "network": 0,
            "files": 0,
            "briefing": 0,
            "timer": 0,
            "decision": 0,
            "websearch": 0,
            "share": 0,
            "quote": 0,
            "meeting": 0,
        }
        self._last_intent = "chat"

        self._on_message = None
        self._on_status = None
        self._on_agent_activate = None

        self.computer_tool = ComputerTool()
        self.news_tool = NewsTool()
        self.security_tool = SecurityTool()
        self.n8n_tool = N8nTool()

        self._init_queen()
        self._first_reply = True
        usage_tracker.start_session()
        tunnel.set_callbacks(on_message=self._tunnel_message)
        tunnel.start()

    def _tunnel_message(self, text: str):
        threading.Thread(target=self._handle_tunnel, args=(text,), daemon=True).start()

    def _handle_tunnel(self, text: str):
        try:
            reply = self.send_text(text)
        except Exception as e:
            reply = f"Error processing request: {e}"
        tunnel.send_to_clients({"status": "reply", "data": reply, "cmd": text})

    def get_time_brief(self) -> str:
        return usage_tracker.get_brief()

    def _cleanup_old_history(self):
        cutoff = datetime.now() - timedelta(hours=self.HISTORY_MAX_AGE_HOURS)
        with self._history_lock:
            kept = []
            for msg in self.history:
                ts = msg.get("timestamp")
                if ts is None:
                    continue
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts)
                    except (ValueError, TypeError):
                        kept.append(msg)
                        continue
                if isinstance(ts, datetime) and ts >= cutoff:
                    kept.append(msg)
            self.history = kept

    def _init_queen(self):
        self.queen = QueenAgent(assistant=self)
        agents = {}
        for label, agent_cls in AGENT_REGISTRY.items():
            agent = agent_cls(assistant=self)
            agents[label] = agent
        self.queen.set_agents(agents)
        if self._on_agent_activate:
            self.queen.set_callback(self._on_agent_activate)

    def set_wake_word(self, enabled: bool):
        """Start or stop wake word detection."""
        self.wake_word_enabled = enabled
        if enabled:
            detector = wake_word_module.get_wake_word_detector()
            if not detector.is_available():
                logger.warning("Wake word detection unavailable (install pyaudio)")
                self.wake_word_enabled = False
                return
            detector.start(callback=self._on_wake_word_detected)
            self._wake_word_detector = detector
            logger.info("Wake word detection started")
        elif self._wake_word_detector:
            self._wake_word_detector.stop()
            self._wake_word_detector = None
            logger.info("Wake word detection stopped")

    def _on_wake_word_detected(self):
        """Called when wake word is detected."""
        if self.listening:
            return
        logger.info("Wake word detected — starting listen")
        threading.Thread(target=self.listen_and_respond, daemon=True).start()

    def set_callbacks(self, on_message=None, on_status=None, on_agent_activate=None):
        self._on_message = on_message
        self._on_status = on_status
        self._on_agent_activate = on_agent_activate
        if hasattr(self, "queen"):
            self.queen.set_callback(on_agent_activate)

    def send_proactive(self):
        msg = proactive.get_proactive_message()
        if msg:
            self._emit_message("assistant", msg)
            if self.speak_responses:
                self._emit_status("speaking", "Speaking...")
                try:
                    use_voice = self.kokoro_voice if self.tts_engine in ("xtts", "kokoro") else self.tts_voice
                    tts.speak(msg, use_voice, self.tts_speed, offline=(self.offline_mode or self.force_offline))
                except Exception as e:
                    logger.warning("TTS failed for proactive message: %s", e)
            return msg
        return None

    def pop_meeting_reminders(self) -> str | None:
        """Returns a combined reminder message for any scheduled meetings now due (marks them notified)."""
        due = meeting_module.get_due_scheduled_meetings()
        if not due:
            return None
        lines = ["⏰ **Meeting reminders:**"]
        for s in due:
            line = f"  📅 {s['title']}"
            if s.get("url"):
                line += f" — {s['url']}"
            lines.append(line)
        return "\n".join(lines)

    def _emit_message(self, role, text):
        if self._on_message:
            self._on_message(role, text)

    def _emit_status(self, state, text):
        if self._on_status:
            self._on_status(state, text)

    def _emit_agent(self, agent_label):
        if self._on_agent_activate:
            self._on_agent_activate(agent_label)

    def _fuzzy_match(self, text, keyword):
        if keyword in text:
            return 1.0
        kw_parts = keyword.split()
        if len(kw_parts) <= 1:
            return 0.0
        text_words = set(text.lower().split())
        matched = sum(1 for kp in kw_parts if kp.lower() in text_words)
        return matched / len(kw_parts)

    def _detect_intent(self, text):
        text_lower = text.lower()
        scores = {}

        text_words = text_lower.split()
        for intent, config in TOOL_DEFINITIONS.items():
            score = 0.0
            for kw in config["keywords"]:
                kw_lower = kw.lower()
                if kw_lower in text_lower:
                    score += config["weight"]
                elif len(kw_lower) > 3 and any(abs(len(w) - len(kw_lower)) <= 2 for w in text_words):
                    similarity = SequenceMatcher(None, kw_lower, text_lower).ratio()
                    if similarity > 0.7:
                        score += config["weight"] * 0.3

            for hint in TOOL_CONTEXT_HINTS.get(intent, []):
                if hint in text_lower:
                    score += 0.3

            if score > 0:
                scores[intent] = score

        if self._last_intent != "chat" and self._last_intent in scores:
            scores[self._last_intent] *= 1.2

        if scores:
            best = max(scores, key=scores.get)
            best = self._disambiguate(text_lower, scores, best)
            self._last_intent = best
            return best

        self._last_intent = "chat"
        return "chat"

    def _disambiguate(self, text_lower, scores, best):
        if len(scores) < 2:
            return best
        SPECIFIC_OVERRIDES = {
            ("computer", "security"): lambda t: "port" in t or "scan" in t or "firewall" in t or "virus" in t,
            ("computer", "sysinfo"): lambda t: "battery" in t or "cpu" in t or "ram" in t or "specs" in t,
            ("computer", "websearch"): lambda t: (
                "open" in t and any(w in t for w in ["website", "url", "browser", "google", "youtube"])
            ),
            ("calculate", "baseconv"): lambda t: "binary" in t or "hex" in t or "octal" in t or "decimal" in t,
            ("timer", "countdown"): lambda t: (
                "event" in t or "birthday" in t or "anniversary" in t or "days until" in t
            ),
            ("music", "computer"): lambda t: "play" in t or "pause" in t or "volume" in t or "next track" in t,
            ("security", "network"): lambda t: "ip" in t or "hostname" in t or "dns" in t or "wifi" in t,
            ("dictation", "meeting"): lambda t: "transcribe" in t or "summarize" in t or "meeting notes" in t,
            ("computer", "list"): lambda t: (
                "process" in t or "task" in t or "ps" in t or "directory" in t or "folder" in t
            ),
        }
        for (a, b), check in SPECIFIC_OVERRIDES.items():
            if a in scores and b in scores:
                if check(text_lower):
                    return a
                elif b in scores and scores[b] >= scores[a]:
                    return b
        sorted_intents = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_two = sorted_intents[:2]
        tie_margin = top_two[0][1] - top_two[1][1]
        if tie_margin > 0.5:
            return best
        # Tie too close — use smart memory to break tie
        try:
            from .core.smart_memory import get_smart_memory

            mem = get_smart_memory()
            correction = mem.get_correction_pattern(text_lower)
            if correction and correction in [i[0] for i in sorted_intents[:5]]:
                return correction
        except Exception:
            pass
        return best

    def _handle_tool(self, intent, text):
        from .assistant_handlers import _handle_tool

        return _handle_tool(self, intent, text)

    def _llm_respond(self, text, context):
        cached = self._get_cached_response(text)
        if cached:
            return cached

        with self._history_lock:
            history_copy = list(self.history)
        sys_prompt = GIRLFRIEND_SYSTEM_PROMPT if self.girlfriend_mode else None

        extra = ""
        if context:
            hist_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_copy[-4:]])
            extra = f"[Memory]\n{context}\n[Recent]\n{hist_text}"

        if self.offline_mode or self.force_offline:
            reply = llm_router.generate_offline(
                text, self.model, history_copy, extra_context=extra, system_prompt=sys_prompt
            )
        else:
            reply = llm_router.generate(
                text,
                self.model,
                history_copy,
                extra_context=extra,
                system_prompt=sys_prompt,
                force_offline=self.force_offline,
            )
        return reply

    def _get_quick_responses(self):
        prefix = self._get_prefix()
        return {
            "hello": f"Hey {prefix}! Ready to roll.",
            "hi": f"Hey {prefix}! Ready to roll.",
            "hey": f"Hey {prefix}! Ready to roll.",
            "good morning": f"Good morning {prefix}! Ready to dominate the day.",
            "good night": f"Good night {prefix}. I'll be here when you wake.",
            "thank you": f"Always, {prefix}.",
            "thanks": f"Always, {prefix}.",
            "who are you": "I'm ORBIT — your AI operating system, built to shock everyone.",
            "what can you do": "I can browse, code, research, control your computer, read PDFs, manage meetings, and more. Try asking!",
            "are you ready": f"Born ready, {prefix}.",
            "status": "All systems nominal. ORBIT 3.0 standing by.",
        }

    def _get_cached_response(self, text):
        key = text.strip().lower()
        return self._get_quick_responses().get(key)

    def _talk_back(self, intent, user_text, result):
        emoji = emotions.get_emotion(emotions.emotion_for_intent(intent))
        prefix = self._get_prefix()

        if intent == "screen":
            return result

        if intent == "screen_share":
            return result

        if intent == "computer":
            if "kill" in user_text.lower() or "stop" in user_text.lower():
                return f"{emoji} {prefix}, process kill kar diya!"
            if "process" in user_text.lower() or "task" in user_text.lower():
                return f"{emoji} {prefix}, running processes:\n{result}"
            if "file" in user_text.lower() or "list" in user_text.lower() or "dir" in user_text.lower():
                return f"{emoji} {prefix}, files:\n{result}"
            if "info" in user_text.lower() or "system" in user_text.lower() or "spec" in user_text.lower():
                return f"{emoji} {prefix}, system info:\n{result}"
            return f"{emoji} {prefix}, done!\n{result}"

        elif intent == "news":
            return f"{emoji} {prefix}, headlines:\n{result}"

        elif intent == "security":
            if "port" in user_text.lower():
                return f"{emoji} {prefix}, open ports:\n{result}"
            if "firewall" in user_text.lower():
                return f"{emoji} {prefix}, firewall:\n{result}"
            return f"{emoji} {prefix}, security:\n{result}"

        elif intent in ("cai", "n8n", "memory"):
            return f"{emoji} {prefix}:\n{result}"

        elif intent == "pipeline":
            return f"{emoji} {prefix}, pipeline complete!\n{result}"

        return f"{emoji} {prefix}:\n{result}"

    def _get_prefix(self):
        from .config import TITLE

        return "Baby" if self.girlfriend_mode else TITLE

    def _process(self, text, speak_it=False):
        intent = self._detect_intent(text)
        usage_tracker.track_message()
        proactive.reset_cooldown()

        if self._first_reply and intent == "chat":
            self._first_reply = False
            brief = usage_tracker.get_brief()
            text = f"{brief}\n\nUser: {text}"

        if intent in self.skill_counts:
            self.skill_counts[intent] += 1
        if speak_it:
            self.skill_counts["voice"] = self.skill_counts.get("voice", 0) + 1

        context = memory_module.get_context_str(3)
        agent_label = agent_label_for_intent(intent)

        try:
            queen_result = self.queen.execute(text, context)
        except Exception as exc:
            logger.warning("Queen execution failed: %s", exc)
            queen_result = None
        if queen_result and isinstance(queen_result, str) and not queen_result.startswith("[Error"):
            reply = queen_result
            self._emit_agent(agent_label if intent != "chat" else "QNN")
        else:
            self._emit_agent(agent_label)

            tool_reply = self._handle_tool(intent, text) if intent != "chat" else ""

            if tool_reply:
                reply = self._talk_back(intent, text, tool_reply)
            else:
                reply = self._llm_respond(text, context)

        reply = self._check_model_error(reply)
        if reply is None:
            reply = "⚠️ No response from model. Please try again."

        self._cleanup_old_history()
        with self._history_lock:
            self.history.append({"role": "user", "content": text, "timestamp": datetime.now()})
            self.history.append({"role": "assistant", "content": reply, "timestamp": datetime.now()})
            if len(self.history) > 50:
                self.history = self.history[-50:]

        memory_module.save_conversation(text, reply, context=intent)
        conv_module.index_message("user", text)
        conv_module.index_message("orbit", reply)

        pending = memory_module.count_pending_cleanup()
        if pending > 0 and intent != "memory":
            reminder = f"\n\n🧹 **{pending} memories** {memory_module.MAX_AGE_DAYS} din se purani hain. 'clear old memories' bolo to clean kar doon."
            reply += reminder

        self._emit_message("assistant", reply)

        if speak_it and reply and not reply.startswith("["):
            self._emit_status("speaking", "Speaking...")
            try:
                sentiment = detect_sentiment(text)
                use_voice = self.kokoro_voice if self.tts_engine in ("xtts", "kokoro") else self.tts_voice
                tts.speak(
                    reply,
                    use_voice,
                    self.tts_speed,
                    offline=(self.offline_mode or self.force_offline),
                    sentiment=sentiment,
                )
            except Exception as e:
                logger.warning("TTS failed for reply: %s", e)

        return reply

    def listen_and_respond(self):
        with self._listen_lock:
            if self.listening:
                return None
            self.listening = True

        try:
            self._emit_status("listening", "Listening...")
            text = stt.listen(mf_index=self.mic_index, offline=(self.offline_mode or self.force_offline))

            if text is None:
                self._emit_status("idle", "Ready")
                return None
            if isinstance(text, str) and text.startswith("["):
                self._emit_message("assistant", text)
                self._emit_status("idle", "Ready")
                return None
            if not text:
                self._emit_status("idle", "Couldn't understand")
                return ""

            self._emit_message("user", text)
            self._emit_status("thinking", "Thinking...")
            reply = self._process(text, speak_it=self.speak_responses)
            self._emit_status("idle", "Ready")
            return reply
        except Exception as e:
            logger.exception("listen_and_respond failed")
            self._emit_status("idle", "Ready")
            return None
        finally:
            self.listening = False
        return reply

    def send_text(self, text):
        self._emit_message("user", text)
        self._emit_status("thinking", "Thinking...")
        reply = self._process(text, speak_it=self.speak_responses)
        self._emit_status("idle", "Ready")
        return reply

    def clear_history(self):
        with self._history_lock:
            self.history = []
        msg = (
            f"🧹 Chat clear, Baby! - {GF_NAME}"
            if self.girlfriend_mode
            else f"🧹 Chat history cleared, {self._get_prefix()}!"
        )
        self._emit_message("assistant", msg)

    def _check_model_error(self, reply):
        if not reply:
            return reply
        err_lower = reply.lower()
        if any(x in err_lower for x in ["does not support image", "cannot read", "image input", "multimodal"]):
            return "⚠️ Current model image nahi samajhta. Use `ollama pull llava` for vision support."
        return reply

    def get_skill_levels(self):
        max_count = max(max(self.skill_counts.values(), default=0), 5)
        levels = {}
        mapping = [
            ("computer", "Computer", 0.5),
            ("news", "News", 0.5),
            ("security", "Security", 0.5),
            ("n8n", "n8n", 0.5),
            ("worldmonitor", "Global Intel", 0.5),
            ("memory", "Memory", 0.5),
            ("voice", "Voice", 0.5),
            ("chat", "Emotions", 0.5),
            ("screen", "Vision", 0.3),
            ("schedule", "Schedule", 0.5),
        ]
        for key, name, base in mapping:
            count = self.skill_counts.get(key, 0)
            levels[name] = round(min(1.0, base + (count / max_count) * 0.5), 2)
        levels["Love"] = 0.7 if self.girlfriend_mode else 0.3
        return levels

    def check_ollama(self):
        models = llm_router.list_ollama_models()
        return len(models) > 0, models

    def get_router_status(self):
        return llm_router.get_status()
