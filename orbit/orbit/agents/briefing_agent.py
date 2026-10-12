# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Daily Briefing Agent — Morning Intelligence Report

Collects tech scout, agent activity, pending approvals, learning lessons, health status.
Generates Tony Stark style morning briefing. Saves to data/briefings/.
"""

from ..core.runtime_paths import data_dir
import json
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

from ..config import TITLE

from ..logging_config import get_logger

logger = get_logger(__name__)

DATA_DIR = data_dir() / "briefings"


@dataclass
class BriefingSection:
    title: str
    icon: str
    items: List[str] = field(default_factory=list)
    status: str = "ok"


class DailyBriefingAgent:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def generate_briefing(self) -> Dict[str, Any]:
        now = datetime.now()
        sections: List[BriefingSection] = []

        sections.append(self._get_tech_scout_section())
        sections.append(self._get_agent_activity_section())
        sections.append(self._get_pending_approvals_section())
        sections.append(self._get_learning_section())
        sections.append(self._get_health_section())

        total_items = sum(len(s.items) for s in sections)
        alerts = sum(1 for s in sections if s.status == "alert")

        briefing = {
            "timestamp": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "greeting": self._greeting(now),
            "sections": [asdict(s) for s in sections],
            "summary": {
                "total_items": total_items,
                "alerts": alerts,
                "status": "alert" if alerts > 0 else "all_clear",
            },
        }

        self._save_briefing(briefing)
        return briefing

    def _greeting(self, now: datetime) -> str:
        hour = now.hour
        name = TITLE
        if hour < 12:
            time_greet = "Good morning"
        elif hour < 17:
            time_greet = "Good afternoon"
        else:
            time_greet = "Good evening"
        return f"{time_greet}, {name}. Here's your daily ORBIT briefing — all systems accounted for."

    def _get_tech_scout_section(self) -> BriefingSection:
        section = BriefingSection(title="Tech Intel", icon="satellite")
        try:
            from ..core.tech_scout import get_scout

            report = get_scout().get_latest()
            if report and report.get("top_findings"):
                for finding in report["top_findings"][:5]:
                    title = finding.get("title", finding.get("name", "Unknown"))
                    score = finding.get("relevance_score", 0)
                    section.items.append(f"{title} (relevance: {score})")
            else:
                section.items.append("No new tech intel. Next scan pending.")
        except Exception as e:
            section.items.append(f"Tech scout unavailable: {e}")
            section.status = "alert"
        return section

    def _get_agent_activity_section(self) -> BriefingSection:
        section = BriefingSection(title="Agent Activity", icon="robot")
        try:
            from ..core.health_check import get_health_system

            dashboard = get_health_system().get_agent_dashboard()
            agent_metrics = dashboard.get("agent_metrics", {})
            if agent_metrics:
                for label, metrics in list(agent_metrics.items())[:8]:
                    calls = metrics.get("calls", 0)
                    status = metrics.get("health", "unknown")
                    section.items.append(f"{label}: {calls} calls, status={status}")
            else:
                section.items.append("No agent activity recorded yet today.")
        except Exception as e:
            section.items.append(f"Agent metrics unavailable: {e}")
        return section

    def _get_pending_approvals_section(self) -> BriefingSection:
        section = BriefingSection(title="Pending Approvals", icon="shield")
        try:
            from ..core.permission import get_permission_manager

            pending = get_permission_manager().get_pending_approvals()
            high = [p for p in pending if p.get("impact") == "HIGH"]
            if high:
                for p in high[:5]:
                    section.items.append(f"HIGH: {p.get('action', 'unknown')} — {p.get('reason', '')}")
                section.status = "alert"
            else:
                section.items.append("No pending HIGH-impact approvals.")
        except Exception:
            section.items.append("Permission system offline.")
        return section

    def _get_learning_section(self) -> BriefingSection:
        section = BriefingSection(title="Learning Updates", icon="brain")
        try:
            from ..core.learning_system import get_learning_system

            ls = get_learning_system()
            lessons = ls.lesson_store.to_dict_list()
            recent = [l for l in lessons if l.get("importance", 0) >= 3][-5:]
            if recent:
                for l in recent:
                    section.items.append(f"[{l.get('importance', '?')}] {l.get('lesson', '')[:80]}")
            else:
                section.items.append("No new lessons learned. System running smoothly.")
        except Exception as e:
            section.items.append(f"Learning system unavailable: {e}")
        return section

    def _get_health_section(self) -> BriefingSection:
        section = BriefingSection(title="System Health", icon="heart_pulse")
        try:
            from ..core.health_check import get_health_system

            dashboard = get_health_system().get_agent_dashboard()
            unhealthy = dashboard.get("unhealthy_agents", [])
            degraded = dashboard.get("degraded_agents", [])
            if unhealthy:
                for a in unhealthy:
                    section.items.append(f"UNHEALTHY: {a}")
                section.status = "alert"
            if degraded:
                for a in degraded:
                    section.items.append(f"DEGRADED: {a}")
                section.status = "alert"
            if not unhealthy and not degraded:
                section.items.append("All agents operational. No issues detected.")
        except Exception:
            section.items.append("Health monitoring offline.")
        return section

    def _save_briefing(self, briefing: Dict):
        today = datetime.now().strftime("%Y-%m-%d")
        path = DATA_DIR / f"{today}.json"
        with self._lock:
            path.write_text(json.dumps(briefing, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Briefing saved", extra={"date": today})

    def get_today(self) -> Optional[Dict]:
        today = datetime.now().strftime("%Y-%m-%d")
        path = DATA_DIR / f"{today}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def get_history(self, days: int = 7) -> List[Dict]:
        results = []
        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            path = DATA_DIR / f"{date}.json"
            if path.exists():
                try:
                    results.append(json.loads(path.read_text(encoding="utf-8")))
                except Exception:
                    continue
        return results


_instance: Optional[DailyBriefingAgent] = None


def get_briefing_agent() -> DailyBriefingAgent:
    global _instance
    if _instance is None:
        _instance = DailyBriefingAgent()
    return _instance
