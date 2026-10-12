# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Emotion and Sentiment Module

This module provides sentiment detection and emotion mapping:

Features:
- Keyword-based sentiment detection (positive/negative/neutral)
- Emotion-to-TTS parameter mapping
- Agent label assignment for intents

Key Functions:
    detect_sentiment(text) - Detect sentiment of text
    get_tts_params(sentiment) - Get TTS parameters for sentiment
    emotion_for_intent(intent) - Get emotion emoji for intent
    agent_label_for_intent(intent) - Get agent code for intent

Usage:
    from orbit.emotions import detect_sentiment, get_tts_params

    sentiment = detect_sentiment("I love this!")
    # Returns: "positive"

    params = get_tts_params("positive")
    # Returns: {"rate": "+10%", "pitch": "+5%"}
"""

EMOTIONS = {
    "greeting": "😊",
    "thinking": "🤔",
    "working": "🛠️",
    "success": "✅",
    "error": "❌",
    "computer": "🖥️",
    "news": "📰",
    "security": "🔒",
    "n8n": "⚡",
    "memory": "🧠",
    "chat": "💬",
    "listening": "🎤",
    "speaking": "🔊",
    "waiting": "⏳",
    "done": "✨",
    "sir": "👑",
    "warning": "⚠️",
    "info": "ℹ️",
    "happy": "🎉",
    "sad": "😔",
    "confused": "😕",
    "celebrate": "🎊",
    "love": "💕",
    "angry": "🔥",
    "calm": "🌊",
    "council": "🏛️",
}

SENTIMENT_KEYWORDS = {
    "excited": [
        "wow",
        "amazing",
        "awesome",
        "great",
        "fantastic",
        "yay",
        "woohoo",
        "🔥",
        "🎉",
        "damm",
        "mast",
        "badhiya",
        "superb",
    ],
    "calm": ["ok", "fine", "sure", "hmm", "acha", "theek", "chill", "relax", "okay"],
    "serious": [
        "urgent",
        "critical",
        "important",
        "danger",
        "alert",
        "warning",
        "emergency",
        "jaldi",
        "fast",
        "immediately",
    ],
    "sad": ["sad", "sorry", "miss", "hurt", "lonely", "dukh", "udaas", "depressed"],
    "happy": ["happy", "love", "thank", "❤️", "😊", "cool", "nice", "maza aaya", "fun"],
    "angry": ["fuck", "shit", "bitch", "ganda", "pagal", "stupid", "hate", "damn"],
}

AGENT_INTENT_MAP = {
    "computer": "TAS",
    "news": "RES",
    "security": "SEC",
    "n8n": "COA",
    "memory": "MEM",
    "cai": "SEC",
    "council": "CNC",
}


def get_emotion(key):
    return EMOTIONS.get(key, "")


def add_emotion(text, key):
    emoji = get_emotion(key)
    if emoji and not text.startswith("["):
        return f"{emoji} {text}"
    return text


def emotion_for_intent(intent):
    mapping = {
        "computer": "computer",
        "news": "news",
        "security": "security",
        "n8n": "n8n",
        "memory": "memory",
        "chat": "chat",
    }
    return mapping.get(intent, "chat")


def detect_sentiment(text):
    text_lower = text.lower()
    scores = {}
    for sentiment, keywords in SENTIMENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[sentiment] = score
    if scores:
        return max(scores, key=scores.get)
    return "neutral"


def get_tts_params(sentiment):
    params = {
        "excited": {"rate_delta": "+30%", "pitch_delta": "+10Hz"},
        "calm": {"rate_delta": "-15%", "pitch_delta": "-5Hz"},
        "serious": {"rate_delta": "-10%", "pitch_delta": "+0Hz"},
        "sad": {"rate_delta": "-20%", "pitch_delta": "-10Hz"},
        "happy": {"rate_delta": "+20%", "pitch_delta": "+5Hz"},
        "angry": {"rate_delta": "+15%", "pitch_delta": "+15Hz"},
        "neutral": {"rate_delta": "+0%", "pitch_delta": "+0Hz"},
    }
    return params.get(sentiment, params["neutral"])


def agent_label_for_intent(intent):
    return AGENT_INTENT_MAP.get(intent, "TAS")
