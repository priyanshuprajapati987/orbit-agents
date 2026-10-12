# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""Screen, Social, and Meeting agents."""

import logging
import re

from ..config import ORBIT_SYSTEM_PROMPT
from ..core.llm import legacy_adapter as llm_router
from .. import screen as screen_module
from .base import BaseAgent

logger = logging.getLogger(__name__)


class ScreenAgent(BaseAgent):
    label = "SCR"
    name = "Screen"
    color = "#06b6d4"
    description = "Screen capture, OCR, visual analysis, UI tree inspection"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the SCREEN specialist.
Your job: Capture screenshots, read screen content via OCR, analyze visual elements.
Always describe what you see on screen clearly."""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._cua = None

    def _get_cua(self):
        if self._cua is None:
            from ..tools.cua_computer import CuaComputerTool, is_available

            if is_available():
                self._cua = CuaComputerTool()
        return self._cua

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "screen",
            "screenshot",
            "dekho",
            "dikhao",
            "screen pe",
            "ocr",
            "read screen",
            "capture",
            "what is on",
            "ui tree",
            "uia",
            "desktop state",
            "apps running",
            "window list",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if intent == "screen":
            return score >= 1
        return score >= 2

    def _run(self, text, context=None):
        text_lower = text.lower()
        cua = self._get_cua()

        if cua and ("ui tree" in text_lower or "uia" in text_lower or "desktop state" in text_lower):
            r = cua.get_desktop_state("orbit")
            if "error" not in r:
                tree = r.get("tree_markdown", "")[:3000]
                return f"**Desktop UI Tree:**\n```\n{tree}\n```"

        if cua and ("apps running" in text_lower or "running apps" in text_lower or "window list" in text_lower):
            r = cua.list_apps()
            if "error" not in r:
                apps = r.get("apps", [])
                lines = [f"**Running Apps ({len(apps)}):**"]
                for a in apps[:20]:
                    icon = "🟢" if a.get("running") else "⚪"
                    lines.append(f"  {icon} **{a.get('name', '?')}** (pid={a.get('pid', '?')})")
                return "\n".join(lines)

        path = screen_module.capture_screen()
        if not path:
            return "❌ Screenshot capture failed."
        analysis = screen_module.analyze_screenshot(path, text)
        screen_module.cleanup(path)
        return analysis or "Screenshot captured but analysis returned no result."


class SocialAgent(BaseAgent):
    label = "SOC"
    name = "Social Media"
    color = "#f472b6"
    description = "Social media automation, posting, scheduling"

    PLATFORMS = {
        "twitter": ["tweet", "twitter", "x.com", "x post"],
        "instagram": ["instagram", "ig post", "insta", "reel"],
        "linkedin": ["linkedin", "professional post"],
        "facebook": ["facebook", "fb post", "fb"],
        "tiktok": ["tiktok", "tik tok"],
        "youtube": ["youtube", "yt", "video post"],
    }

    SOCIAL_ACTIONS = {
        "post": "post",
        "publish": "post",
        "tweet": "tweet",
        "schedule": "schedule",
        "share": "share",
        "automate": "automate",
    }

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the SOCIAL MEDIA specialist.
Your job: Create content, schedule posts, manage social platforms.
Integrate with n8n and Postiz for actual posting automation.
Optimize content for each platform's style."""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._tool = None

    def _get_tool(self):
        if self._tool is None:
            from ..tools import N8nTool

            self._tool = N8nTool()
        return self._tool

    def _detect_platforms(self, text):
        text_lower = text.lower()
        return [pname for pname, pwords in self.PLATFORMS.items() if any(pw in text_lower for pw in pwords)]

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "social",
            "post",
            "tweet",
            "instagram",
            "facebook",
            "linkedin",
            "postiz",
            "content",
            "schedule post",
            "social media",
        ]
        text_lower = text.lower()
        return sum(1 for kw in keywords if kw in text_lower) >= 1

    def _run(self, text, context=None):
        text_lower = text.lower()
        tool = self._get_tool()
        detected_platforms = self._detect_platforms(text)
        n8n_online = False

        if tool:
            try:
                status = tool.check_status()
                n8n_online = status.get("online", False)

                if not n8n_online:
                    pass
                else:
                    wf_match = re.search(r"(?:workflow|wf|trigger|run|execute)\s+[`\"']?([^\s`\"']+)", text_lower)
                    if wf_match:
                        wf_name = wf_match.group(1)
                        result = tool.trigger_workflow_by_name(
                            wf_name, {"text": text, "source": "SOC", "platforms": detected_platforms}
                        )
                        return f"Social workflow triggered:\n{result}"

                    if any(kw in text_lower for kw in ["workflow", "list workflows", "show workflows"]):
                        result = tool.handle_natural("list workflows")
                        return f"Available social workflows:\n{result}"

                    action_match = next(
                        (action for keyword, action in self.SOCIAL_ACTIONS.items() if keyword in text_lower), None
                    )

                    if action_match:
                        result = tool.trigger_workflow_by_name(
                            action_match, {"text": text, "source": "SOC", "platforms": detected_platforms}
                        )
                        if result and "not found" not in result.lower():
                            return f"Social workflow triggered ({action_match}):\n{result}"

                        result = tool.handle_natural(text)
                        if result and "error" not in result.lower() and "I can:" not in result:
                            return f"n8n result:\n{result}"

            except Exception as e:
                logger.warning(f"SOC tool error: {e}")

        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        if detected_platforms:
            sys_prompt += (
                f"\n\n[Target Platforms] {', '.join(detected_platforms)}. "
                "Optimize content length, tone, hashtags, and formatting "
                "for each platform."
            )
        if n8n_online:
            sys_prompt += (
                "\n\n[Note] n8n is online. If this content needs to be "
                "posted, suggest a workflow command like "
                "'trigger workflow [name]' to publish it."
            )
        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)


class MeetingAgent(BaseAgent):
    label = "MTG"
    name = "Meeting Assistant"
    color = "#00ff88"
    description = "Meeting management, real-time transcription, summaries, simulations"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the MEETING specialist.
Your job: Help the user attend meetings, record and transcribe discussions, summarize decisions, extract action items, or simulate meetings for preparation.
Always keep responses professional and highlight critical information."""

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "meeting",
            "zoom",
            "teams",
            "google meet",
            "transcribe",
            "summarize meeting",
            "meeting notes",
            "simulate meeting",
            "show meetings",
            "list meetings",
            "delete meeting",
            "meeting history",
            "meeting status",
            "record meeting",
            "search meeting",
            "export meeting",
            "export all meetings",
            "meeting action items",
            "meeting to notes",
            "save meeting to notes",
            "meeting stats",
            "schedule meeting",
            "scheduled meetings",
            "upcoming meetings",
            "remind me about meeting",
            "meeting brief",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if intent == "meeting":
            return score >= 1
        return score >= 2

    def _run(self, text, context=None):
        from .. import meeting as meeting_module

        text_lower = text.lower()

        # Calendar tool integration
        if any(
            kw in text_lower
            for kw in ["calendar", "event", "schedule", "appointment", "today's schedule", "upcoming events"]
        ):
            try:
                from ..tools.calendar_tool import CalendarTool

                cal = CalendarTool()
                if "create" in text_lower or "add" in text_lower or "schedule" in text_lower:
                    import re as _re

                    title_m = _re.search(
                        r"(?:create|add|schedule)\s+(?:event|meeting|appointment)\s+[`\"']?([^`\"'\n]+)",
                        text,
                        _re.IGNORECASE,
                    )
                    title = title_m.group(1).strip().rstrip(".,;:") if title_m else "Event"
                    date_m = _re.search(r"(?:on|for|at)\s+(\d{4}-\d{2}-\d{2})", text_lower)
                    date = date_m.group(1) if date_m else None
                    result = cal.create_event(title=title, date=date)
                    if result and result.get("success"):
                        return f"📅 Event created: {title}" + (f" on {date}" if date else "")
                    return f"⚠️ Could not create event: {result}"
                elif "today" in text_lower:
                    events = cal.get_today()
                    if events:
                        lines = [f"📅 Today's events ({len(events)}):"]
                        for e in events:
                            lines.append(f"  • {e.get('title', 'Untitled')} — {e.get('time', '?')}")
                        return "\n".join(lines)
                    return "📅 No events today."
                elif "upcoming" in text_lower or "list" in text_lower:
                    events = cal.get_upcoming()
                    if events:
                        lines = [f"📅 Upcoming events ({len(events)}):"]
                        for e in events[:10]:
                            lines.append(f"  • {e.get('title', 'Untitled')} — {e.get('date', '?')}")
                        return "\n".join(lines)
                    return "📅 No upcoming events."
                else:
                    events = cal.list_events()
                    return f"📅 {len(events)} events found." if events else "📅 No events."
            except Exception as e:
                return f"⚠️ Calendar tool error: {e}"

        text_lower = text.lower()

        if "join" in text_lower:
            m = re.search(r"(https?://\S+)", text)
            url = m.group(1) if m else ""
            if not url:
                return "Pehle URL share karo."
            ok = meeting_module.join_meeting(url)
            if not ok:
                return "❌ URL open nahi ho payi."
            if "record" in text_lower or "and record" in text_lower:
                res = meeting_module.start_meeting()
                if res.get("success"):
                    return f"🤝 Link open kiya + 🎙️ recording start: {url}"
                return f"🤝 Link open kiya par recording start nahi ho payi: {res.get('error')}"
            return f"🤝 Meeting link open kiya: {url}"

        if "start" in text_lower or "listen" in text_lower or "record" in text_lower:
            title = None
            if "start meeting" in text_lower:
                t = text_lower.split("start meeting", 1)[1].strip().strip('"').strip("'")
                if t:
                    title = t
            elif "record" in text_lower:
                t = text_lower.split("record", 1)[1].replace("meeting", "").strip().strip('"').strip("'")
                if t:
                    title = t
            res = meeting_module.start_meeting(title=title)
            if res.get("success"):
                return f"🎙️ Meeting recording start kar di! Title: {res.get('title')}"
            return f"⚠️ Status: {res.get('error')}"

        if "stop" in text_lower or "end" in text_lower or "summarize" in text_lower:
            res = meeting_module.stop_meeting()
            if not res.get("success"):
                return f"⚠️ Error: {res.get('error')}"
            if "meeting" in res:
                m = res["meeting"]
                lines = [f"🎙️ Meeting '{m.get('title', 'Untitled')}' khatam!"]
                if m.get("duration_seconds"):
                    mins = m["duration_seconds"] // 60
                    secs = m["duration_seconds"] % 60
                    lines.append(f"⏱ Duration: {mins}m {secs}s")
                if m.get("summary"):
                    lines.append(f"\n📋 Summary:\n{m['summary']}")
                if m.get("action_items"):
                    lines.append("\n✅ Action Items:")
                    for item in m["action_items"]:
                        lines.append(f"  - {item}")
                return "\n".join(lines)
            return "✅ Meeting stopped."

        if "delete" in text_lower:
            m = re.search(r"delete\s+meeting\s+[`\"']?([^\s`\"']+)", text_lower)
            if m:
                ok = meeting_module.delete_meeting(m.group(1))
                return "🗑️ Meeting delete ho gayi!" if ok else "❌ Meeting nahi mili."
            return "Kaunsi delete karni hai? ID batao."

        if "history" in text_lower or "show meetings" in text_lower or "list meetings" in text_lower:
            meetings = meeting_module.list_meetings()
            if not meetings:
                return "No meetings recorded yet."
            lines = [f"📋 Meeting History ({len(meetings)}):\n"]
            for mt in meetings[-10:]:
                title = mt.get("title", "Untitled")
                date = mt.get("date", "?")
                dur = mt.get("duration_seconds", 0)
                mins = dur // 60 if dur else 0
                lines.append(f"  • {title} ({date}) — {mins} min")
            return "\n".join(lines)

        if "search" in text_lower:
            query = re.sub(r"\b(search|meeting|for|the|about)\b", "", text_lower).strip()
            if query:
                results = meeting_module.search_meetings(query)
                if results:
                    lines = [f"🔍 Meeting search '{query}':\n"]
                    for mt in results[:5]:
                        lines.append(f"  • {mt.get('title', 'Untitled')} ({mt.get('date', '?')})")
                    return "\n".join(lines)
                return f"❌ '{query}' se koi meeting nahi mili."

        if "export" in text_lower:
            if "all" in text_lower:
                result = meeting_module.export_all_meetings()
                if result:
                    return f"📁 All meetings exported:\n{str(result)[:500]}"
                return "❌ Export failed."
            m = re.search(r"export\s+meeting\s+[`\"']?([^\s`\"']+)", text_lower)
            if m:
                result = meeting_module.export_meeting(m.group(1))
                if result:
                    return f"📁 Meeting exported:\n{str(result)[:500]}"
                return "❌ Export failed."
            return "Kaunsi export karni hai? ID batao ya 'export all meetings' bolo."

        if "action items" in text_lower or "action items" in text_lower:
            meetings = meeting_module.list_meetings()
            if not meetings:
                return "No meetings with action items."
            items = []
            for mt in meetings:
                for item in mt.get("action_items", []):
                    items.append(f"  • [{mt.get('title', '?')}] {item}")
            if items:
                return "✅ All Action Items:\n" + "\n".join(items[:20])
            return "No action items found in any meeting."

        if "save meeting to notes" in text_lower or "meeting to notes" in text_lower:
            m = re.search(r"save\s+meeting\s+[`\"']?([^\s`\"']+)", text_lower)
            if m:
                result = meeting_module.save_meeting_to_notes(m.group(1))
                if result and result.get("success"):
                    return f"📝 Meeting saved to notes: {result.get('path', '?')}"
                return "❌ Save failed."
            return "Kaunsi save karni hai? ID batao."

        if "simulate" in text_lower or "practice" in text_lower:
            topic = re.sub(r"\b(simulate|practice|meeting|about|on)\b", "", text_lower).strip()
            if not topic:
                return "Kis topic pe simulate karna hai? Batao."
            sys_prompt = self.DEFAULT_PROMPT
            if context:
                sys_prompt += f"\n\n[Context]\n{context}"
            sys_prompt += f"\n\n[Task] Simulate a meeting discussion about: {topic}"
            return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)

        if "stats" in text_lower or "statistics" in text_lower:
            meetings = meeting_module.list_meetings()
            if not meetings:
                return "No meeting stats available."
            total = len(meetings)
            total_dur = sum(m.get("duration_seconds", 0) for m in meetings)
            avg_dur = total_dur // total if total else 0
            total_items = sum(len(m.get("action_items", [])) for m in meetings)
            lines = [
                "📊 Meeting Stats:",
                f"  Total meetings: {total}",
                f"  Total duration: {total_dur // 3600}h {(total_dur % 3600) // 60}m",
                f"  Average duration: {avg_dur // 60}m {avg_dur % 60}s",
                f"  Total action items: {total_items}",
            ]
            return "\n".join(lines)

        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)
