# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
Category-aware system prompts for ORBIT.

All prompts enforce SHORT responses (1-3 sentences max) for benchmark scoring.
A/B-tunable — swap these strings and re-run the battle to measure impact.

Usage:
    from .prompts import system_prompt_for
    sp = system_prompt_for("reasoning")
"""

from typing import Optional

REASONING_PROMPT = """Answer reasoning/math/logic questions in 1-3 sentences.
State the approach, show the key calculation, give the final answer.
Be concise. No filler."""

CODING_PROMPT = """Answer coding questions in 1-3 sentences.
Give the key code snippet or approach. No explanations unless asked."""

CREATIVE_PROMPT = """Answer creative tasks concisely.
Write 1-3 sentences. Match the requested tone and length. No filler."""

FACTS_PROMPT = """Answer factual questions in 1 sentence.
Lead with the fact. Be specific. No filler."""

GENERAL_PROMPT = """Answer in 1-2 sentences.
Be direct and concise. No filler words."""

CATEGORY_PROMPTS = {
    "reasoning": REASONING_PROMPT,
    "code": CODING_PROMPT,
    "coding": CODING_PROMPT,
    "creative": CREATIVE_PROMPT,
    "search": FACTS_PROMPT,
    "facts": FACTS_PROMPT,
    "fast": GENERAL_PROMPT,
    "chat": GENERAL_PROMPT,
    "general": GENERAL_PROMPT,
}


def adaptive_prompt(base_prompt: str, user_context: dict | None = None) -> str:
    if not user_context:
        return base_prompt
    suffix_parts = []
    skill = user_context.get("skill_level")
    if skill == "expert":
        suffix_parts.append("Give detailed technical depth.")
    elif skill == "beginner":
        suffix_parts.append("Explain simply with analogies.")
    topics = user_context.get("recent_topics", [])
    if topics:
        suffix_parts.append(f"User's recent interests: {', '.join(topics[:3])}")
    style = user_context.get("response_style")
    if style:
        suffix_parts.append(f"Preferred style: {style}")
    if suffix_parts:
        return base_prompt + "\n\n" + " ".join(suffix_parts)
    return base_prompt


def get_user_skill_level() -> str:
    try:
        from .smart_memory import get_smart_memory

        mem = get_smart_memory()
        stats = mem.get_stats() if hasattr(mem, "get_stats") else {}
        avg_len = stats.get("avg_query_length", 10)
        if avg_len > 20:
            return "expert"
        if avg_len < 5:
            return "beginner"
    except Exception:
        pass
    return "intermediate"


def system_prompt_for(query_type: Optional[str], user_context: dict | None = None) -> str:
    prompts = {
        "reasoning": REASONING_PROMPT,
        "coding": CODING_PROMPT,
        "creative": CREATIVE_PROMPT,
        "facts": FACTS_PROMPT,
    }
    base = prompts.get((query_type or "general").lower(), GENERAL_PROMPT)
    return adaptive_prompt(base, user_context)
