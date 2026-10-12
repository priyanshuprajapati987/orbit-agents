# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Emotions - thin re-export of the canonical inner module.

Single source of truth: `orbit.orbit.emotions`.
This module exists only as a compatibility shim so outer-level imports
(`from orbit.emotions import X`) keep working without a duplicated copy.
"""

from orbit.orbit.emotions import (  # noqa: F401
    AGENT_INTENT_MAP,
    EMOTIONS,
    SENTIMENT_KEYWORDS,
    agent_label_for_intent,
    detect_sentiment,
    emotion_for_intent,
    get_tts_params,
)
