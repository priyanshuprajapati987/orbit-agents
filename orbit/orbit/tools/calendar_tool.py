# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Calendar Tool

Manage calendar events locally.
Supports: create, list, update, delete, search events.
Events stored in JSON file with recurring event support.
"""

from ..core.runtime_paths import data_dir
import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from ..logging_config import get_logger

logger = get_logger(__name__)


class CalendarTool:
    def __init__(self, storage_file: str = None):
        self.name = "calendar"
        self.description = "Manage calendar events and reminders"
        self.storage_file = storage_file or str(data_dir() / "calendar.json")
        self._lock = threading.Lock()
        self.events = self._load()

    def _load(self) -> Dict:
        try:
            p = Path(self.storage_file)
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Failed to load calendar: %s", e)
        return {}

    def _save(self):
        try:
            p = Path(self.storage_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.events, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error("Failed to save calendar: %s", e)

    def create_event(
        self,
        title: str,
        date: str,
        time: str = "00:00",
        duration_minutes: int = 60,
        description: str = "",
        recurrence: str = "",
        reminder_minutes: int = 15,
    ) -> Dict:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except (ValueError, TypeError):
            date = datetime.now().strftime("%Y-%m-%d")
        event_id = f"evt_{uuid.uuid4().hex[:8]}"
        event = {
            "id": event_id,
            "title": title,
            "date": date,
            "time": time,
            "duration_minutes": duration_minutes,
            "description": description,
            "recurrence": recurrence,
            "reminder_minutes": reminder_minutes,
            "created_at": datetime.now().isoformat(),
            "completed": False,
        }
        with self._lock:
            self.events[event_id] = event
            self._save()
        return {"success": True, "event": event}

    def get_event(self, event_id: str) -> Optional[Dict]:
        with self._lock:
            return self.events.get(event_id)

    def list_events(self, date_from: str = None, date_to: str = None, include_completed: bool = False) -> List[Dict]:
        with self._lock:
            events = list(self.events.values())
        if not include_completed:
            events = [e for e in events if not e.get("completed")]
        if date_from:
            events = [e for e in events if e.get("date", "") >= date_from]
        if date_to:
            events = [e for e in events if e.get("date", "") <= date_to]
        return sorted(events, key=lambda x: (x.get("date", ""), x.get("time", "")))

    def update_event(self, event_id: str, **kwargs) -> bool:
        with self._lock:
            if event_id not in self.events:
                return False
            allowed = {
                "title",
                "date",
                "time",
                "duration_minutes",
                "description",
                "recurrence",
                "reminder_minutes",
                "completed",
            }
            for key, value in kwargs.items():
                if key in allowed:
                    self.events[event_id][key] = value
            self._save()
            return True

    def delete_event(self, event_id: str) -> bool:
        with self._lock:
            if event_id not in self.events:
                return False
            del self.events[event_id]
            self._save()
            return True

    def search_events(self, query: str) -> List[Dict]:
        query_lower = query.lower()
        with self._lock:
            events = list(self.events.values())
        return [
            e
            for e in events
            if query_lower in e.get("title", "").lower() or query_lower in e.get("description", "").lower()
        ]

    def get_upcoming(self, days: int = 7) -> List[Dict]:
        today = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        return self.list_events(date_from=today, date_to=future)

    def get_today(self) -> List[Dict]:
        today = datetime.now().strftime("%Y-%m-%d")
        return self.list_events(date_from=today, date_to=today)

    def complete_event(self, event_id: str) -> bool:
        return self.update_event(event_id, completed=True)
