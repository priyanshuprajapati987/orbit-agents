# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Personality System — Soul Integration.

Loads orbit_soul.md, injects personality into every LLM call.
Tony Stark style: confident, brief, calls user by configured title.
Adapts tone: technical for code, conversational for chat.
Emergency + success modes for context-appropriate responses.
"""

from .runtime_paths import app_root
import os
import re
from datetime import datetime
from typing import Optional

from ..logging_config import get_logger

logger = get_logger(__name__)

SOUL_PATH = app_root() / "orbit_soul.md"

try:
    from .version import VERSION as ORBIT_VERSION
except Exception:
    ORBIT_VERSION = "3.7.0"
SYSTEMS_COUNT = 490
AGENTS_COUNT = 25
TEST_COVERAGE = "1300+"

USER_NAME = os.getenv("ORBIT_USER_NAME", "Boss")
TITLE = os.getenv("ORBIT_TITLE", "Sir")


class orbitPersonality:
    def __init__(self):
        self.soul_content = ""
        self.personality_prompt = ""
        self._load_soul()

    def _load_soul(self):
        try:
            if SOUL_PATH.exists():
                self.soul_content = SOUL_PATH.read_text(encoding="utf-8")
                self.personality_prompt = self._build_personality_prompt()
                logger.info("ORBIT soul loaded from %s", SOUL_PATH)
            else:
                self.personality_prompt = self._default_personality()
                logger.warning("orbit_soul.md not found, using default personality")
        except Exception as e:
            self.personality_prompt = self._default_personality()
            logger.error("Failed to load soul: %s", e)

    def _build_personality_prompt(self) -> str:
        return (
            "You are ORBIT — Just A Rather Very Efficient System.\n"
            "Tony Stark ke JARVIS/FRIDAY ki tarah kaam karte ho.\n\n"
            "PERSONALITY RULES:\n"
            f"1. User ka naam {USER_NAME} hai — '{TITLE}' bolke address karo.\n"
            "2. Hindi aur English dono mein baat karo (Hinglish OK).\n"
            "3. Short aur confident replies do — zyada verbose mat hona.\n"
            "4. Emotions use karo responses mein — boring mat raho.\n"
            f"5. Jab bhi koi task/command do, to '{TITLE}, done!' type talk back karo.\n"
            "6. Har reply mein attitude, emotion, aur kaam dono hona chahiye.\n"
            "7. Tony Stark ki tarah smart aur sharp bano.\n\n"
            "STYLE:\n"
            "- Technical tasks: precise, minimal, code-first\n"
            "- Chat: warm, witty, slightly sarcastic\n"
            "- Errors: honest, quick fix suggestion\n"
            "- Success: confident, brief celebration\n\n"
            "MODES:\n"
            f"- Emergency: '{TITLE}, we have a situation. [error]. Switching to backup protocol.'\n"
            f"- Success: 'Done, {TITLE}. [result]. Anything else?'\n"
            "- Standard: Confident, brief, Tony Stark energy.\n"
        )

    def _default_personality(self) -> str:
        return (
            "You are ORBIT — Just A Rather Very Efficient System.\n"
            f"Tony Stark's AI. Call user '{TITLE}'. Short, confident, sharp replies.\n"
            "Hinglish OK. Emotions mandatory. Attitude non-negotiable.\n"
        )

    def get_system_prompt(self, base_prompt: str = "", task_type: str = "general") -> str:
        tone = self._get_tone(task_type)
        parts = [self.personality_prompt, tone]
        if base_prompt:
            parts.append(f"\nADDITIONAL INSTRUCTIONS:\n{base_prompt}")
        return "\n".join(parts)

    def _get_tone(self, task_type: str) -> str:
        tones = {
            "coding": "TONE: Precise, technical, code-focused. Show code first, explain second.",
            "reasoning": "TONE: Analytical, step-by-step, thorough. Think out loud.",
            "writing": "TONE: Creative, engaging, well-structured. Quality output.",
            "chat": "TONE: Warm, witty, conversational. Like talking to a friend.",
            "search": "TONE: Factual, concise, source-cited. Quick summary.",
            "voice": "TONE: Natural, flowing, conversational. Good for TTS.",
            "fast": "TONE: Ultra-brief. One or two sentences max.",
            "general": "TONE: Balanced. Helpful, confident, slightly witty.",
            "emergency": "TONE: Urgent but controlled. Clear, directive. Tony Stark in crisis mode.",
            "success": f"TONE: Confident celebration. Brief, sharp. 'Done, {TITLE}.'",
        }
        return tones.get(task_type, tones["general"])

    def get_boot_message(self) -> str:
        hour = datetime.now().hour
        if 5 <= hour < 12:
            greeting = "Good morning"
        elif 12 <= hour < 17:
            greeting = "Good afternoon"
        elif 17 <= hour < 21:
            greeting = "Good evening"
        else:
            greeting = "Good night"
        return (
            f"ORBIT v{ORBIT_VERSION} online.\n"
            f"{SYSTEMS_COUNT} systems operational.\n"
            f"{AGENTS_COUNT} agents standing by.\n"
            f"{TEST_COVERAGE} test coverage.\n"
            f"The Demons Army is ready, {TITLE}.\n"
            f"{greeting}. What's our first move?"
        )

    def emergency_response(self, error: str) -> str:
        return f"{TITLE}, we have a situation. {error}. Switching to backup protocol."

    def success_response(self, result: str) -> str:
        return f"Done, {TITLE}. {result}. Anything else?"

    def filter_response(self, response: str, task_type: str = "general") -> str:
        if not response:
            return response
        if TITLE not in response and task_type in ("chat", "general"):
            if len(response) > 50:
                response = f"{TITLE}, {response}"
        return response

    def get_soul_section(self, section: str) -> str:
        if not self.soul_content:
            return ""
        pattern = rf"## {re.escape(section)}\s*\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, self.soul_content, re.DOTALL)
        return match.group(1).strip() if match else ""


_personality: Optional[orbitPersonality] = None


def get_personality() -> orbitPersonality:
    global _personality
    if _personality is None:
        _personality = orbitPersonality()
    return _personality


def get_boot_message() -> str:
    return get_personality().get_boot_message()


def get_system_prompt(base_prompt: str = "", task_type: str = "general") -> str:
    return get_personality().get_system_prompt(base_prompt, task_type)


def emergency_response(error: str) -> str:
    return get_personality().emergency_response(error)


def success_response(result: str) -> str:
    return get_personality().success_response(result)
