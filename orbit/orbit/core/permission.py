# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Permission System

3-Level Permission System:
- LOW: Auto-execute (read files, notes, safe operations)
- MEDIUM: Inform + auto-continue (web search, API calls, file creation)
- HIGH: Priyanshu approval required (install, publish, delete, system changes)

Human is always the final authority. ORBIT commands, agents execute.
"""

from .runtime_paths import app_root
import json
import threading
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Callable
from pathlib import Path
from ..logging_config import get_logger

logger = get_logger(__name__)

# Ring cap for permission_log.json — bounds file size (~500 entries) and the
# per-write O(n) cost; history/pending read the tail anyway.
MAX_LOG_ENTRIES = 500
# Newest pending approvals returned to the panel.
MAX_PENDING = 50


class PermissionLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PermissionAction:
    def __init__(self, name: str, level: PermissionLevel, description: str = ""):
        self.name = name
        self.level = level
        self.description = description


IMPACT_LEVELS: Dict[str, PermissionAction] = {
    # LOW IMPACT - Auto-execute
    "read_file": PermissionAction("read_file", PermissionLevel.LOW, "Read file contents"),
    "write_file": PermissionAction("write_file", PermissionLevel.LOW, "Write/create files"),
    "organize_notes": PermissionAction("organize_notes", PermissionLevel.LOW, "Organize notes"),
    "create_draft": PermissionAction("create_draft", PermissionLevel.LOW, "Create drafts"),
    "web_search": PermissionAction("web_search", PermissionLevel.LOW, "Web search"),
    "local_workflow": PermissionAction("local_workflow", PermissionLevel.LOW, "Run safe local workflows"),
    "get_news": PermissionAction("get_news", PermissionLevel.LOW, "Get news"),
    "set_timer": PermissionAction("set_timer", PermissionLevel.LOW, "Set timer/alarm"),
    "take_note": PermissionAction("take_note", PermissionLevel.LOW, "Take notes"),
    "calculate": PermissionAction("calculate", PermissionLevel.LOW, "Calculations"),
    "get_weather": PermissionAction("get_weather", PermissionLevel.LOW, "Weather info"),
    "system_info": PermissionAction("system_info", PermissionLevel.LOW, "System information"),
    "screenshot": PermissionAction("screenshot", PermissionLevel.LOW, "Take screenshot"),
    "clipboard": PermissionAction("clipboard", PermissionLevel.LOW, "Clipboard operations"),
    # MEDIUM IMPACT - Inform + auto-continue
    "api_call": PermissionAction("api_call", PermissionLevel.MEDIUM, "API calls (free tier)"),
    "file_creation": PermissionAction("file_creation", PermissionLevel.MEDIUM, "Create files"),
    "schedule_change": PermissionAction("schedule_change", PermissionLevel.MEDIUM, "Schedule changes"),
    "send_email": PermissionAction("send_email", PermissionLevel.MEDIUM, "Send emails"),
    "post_message": PermissionAction("post_message", PermissionLevel.MEDIUM, "Post messages"),
    "run_script": PermissionAction("run_script", PermissionLevel.MEDIUM, "Run scripts"),
    "install_package": PermissionAction("install_package", PermissionLevel.MEDIUM, "Install packages"),
    "update_config": PermissionAction("update_config", PermissionLevel.MEDIUM, "Update configuration"),
    # HIGH IMPACT - Priyanshu approval required
    "api_call_paid": PermissionAction("api_call_paid", PermissionLevel.HIGH, "Paid API calls"),
    "publish_content": PermissionAction("publish_content", PermissionLevel.HIGH, "Publish content"),
    "delete_file": PermissionAction("delete_file", PermissionLevel.HIGH, "Delete files"),
    "system_change": PermissionAction("system_change", PermissionLevel.HIGH, "System changes"),
    "install_software": PermissionAction("install_software", PermissionLevel.HIGH, "Install software"),
    "external_comms": PermissionAction("external_comms", PermissionLevel.HIGH, "External communications"),
    "financial": PermissionAction("financial", PermissionLevel.HIGH, "Financial operations"),
    "security_change": PermissionAction("security_change", PermissionLevel.HIGH, "Security changes"),
}


class PermissionManager:
    def __init__(self, log_file: Optional[str] = None):
        self.log_file = log_file or str(app_root() / "logs" / "permission_log.json")
        self._lock = threading.Lock()
        self._ensure_log_dir()
        self.custom_rules: Dict[str, PermissionLevel] = {}

    def _ensure_log_dir(self):
        log_dir = Path(self.log_file).parent
        log_dir.mkdir(exist_ok=True)

    def get_permission_level(self, action: str) -> PermissionLevel:
        with self._lock:
            custom = self.custom_rules.get(action)
        if custom is not None:
            return custom

        if action in IMPACT_LEVELS:
            return IMPACT_LEVELS[action].level

        logger.warning("Unknown action '%s' - defaulting to HIGH", action)
        return PermissionLevel.HIGH

    def set_custom_rule(self, action: str, level: PermissionLevel):
        with self._lock:
            self.custom_rules[action] = level
        logger.info("Custom rule set: %s -> %s", action, level.value)

    def check_permission(self, action: str, details: str = "") -> Dict:
        level = self.get_permission_level(action)
        timestamp = datetime.now().isoformat()

        result = {
            "action": action,
            "level": level.value,
            "details": details,
            "timestamp": timestamp,
            "approved": False,
            "approved_by": None,
            "reason": "",
        }

        if level == PermissionLevel.LOW:
            result["approved"] = True
            result["approved_by"] = "auto"
            result["reason"] = "Low impact - auto-approved"
            self._log_permission(result)
            return result

        elif level == PermissionLevel.MEDIUM:
            result["approved"] = True
            result["approved_by"] = "auto"
            result["reason"] = "Medium impact - informed and proceeding"
            self._log_permission(result)
            return result

        else:  # HIGH
            result["approved"] = False
            result["approved_by"] = None
            result["reason"] = "High impact - requires Priyanshu approval"
            self._log_permission(result)
            return result

    def require_approval(self, action: str, details: str = "", callback: Optional[Callable] = None):
        result = self.check_permission(action, details)

        if not result["approved"]:
            logger.warning("ACTION REQUIRES APPROVAL: %s - %s", action, details)
            if callback:
                return callback(result)
            return result

        return result

    def approve_action(self, action: str, approved_by: str = "Priyanshu", reason: str = "Manual approval"):
        now = datetime.now().isoformat()
        resolved = 0
        with self._lock:
            entries = self._read_log_unlocked()
            for e in entries:
                if (e.get("action") == action and e.get("level") == "high"
                        and not e.get("approved") and not e.get("denied")):
                    e["approved"] = True
                    e["approved_by"] = approved_by
                    e["reason"] = reason
                    e["decided_at"] = now
                    resolved += 1
            if resolved:
                self._write_log_unlocked(entries)
        # Audit record of the decision itself (approved=True => never pending).
        result = {
            "action": action,
            "level": "high",
            "timestamp": now,
            "approved": True,
            "approved_by": approved_by,
            "reason": reason,
            "resolved": resolved,
        }
        self._log_permission(result)
        logger.info("Action APPROVED: %s by %s (%d pending resolved)", action, approved_by, resolved)
        return result

    def deny_action(self, action: str, denied_by: str = "Priyanshu", reason: str = "Manual denial"):
        now = datetime.now().isoformat()
        resolved = 0
        with self._lock:
            entries = self._read_log_unlocked()
            for e in entries:
                if (e.get("action") == action and e.get("level") == "high"
                        and not e.get("approved") and not e.get("denied")):
                    e["denied"] = True
                    e["approved_by"] = denied_by
                    e["reason"] = reason
                    e["decided_at"] = now
                    resolved += 1
            if resolved:
                self._write_log_unlocked(entries)
        result = {
            "action": action,
            "level": "high",
            "timestamp": now,
            "approved": False,
            "denied": True,
            "approved_by": denied_by,
            "reason": reason,
            "resolved": resolved,
        }
        self._log_permission(result)
        logger.warning("Action DENIED: %s by %s - %s (%d pending resolved)", action, denied_by, reason, resolved)
        return result

    def _read_log_unlocked(self) -> list:
        """Read the log file. Caller must hold self._lock."""
        log_path = Path(self.log_file)
        if not log_path.exists():
            return []
        with open(log_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_log_unlocked(self, entries: list):
        """Write entries with a ring cap. Caller must hold self._lock."""
        if len(entries) > MAX_LOG_ENTRIES:
            entries = entries[-MAX_LOG_ENTRIES:]
        log_path = Path(self.log_file)
        log_path.parent.mkdir(exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        return entries

    def _log_permission(self, entry: Dict):
        try:
            with self._lock:
                entries = self._read_log_unlocked()
                entries.append(entry)
                self._write_log_unlocked(entries)
        except Exception as e:
            logger.error("Failed to log permission: %s", e)

    def get_pending_approvals(self):
        try:
            with self._lock:
                entries = self._read_log_unlocked()

            pending = [
                e for e in entries
                if e.get("level") == "high" and not e.get("approved") and not e.get("denied")
            ]
            # Cap the list — the log is an event stream, unbounded pending lists
            # made the panel unusable (800+ cards, re-rendered every poll).
            return pending[-MAX_PENDING:]

        except Exception as e:
            logger.error("Failed to get pending approvals: %s", e)
            return []

    def get_permission_history(self, limit: int = 50):
        try:
            log_path = Path(self.log_file)
            if not log_path.exists():
                return []

            with open(log_path, "r", encoding="utf-8") as f:
                entries = json.load(f)

            return entries[-limit:]

        except Exception as e:
            logger.error("Failed to get permission history: %s", e)
            return []


_permission_manager = None


def get_permission_manager() -> PermissionManager:
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = PermissionManager()
    return _permission_manager


def check_permission(action: str, details: str = "") -> Dict:
    return get_permission_manager().check_permission(action, details)


def require_approval(action: str, details: str = "", callback: Optional[Callable] = None):
    return get_permission_manager().require_approval(action, details, callback)


def approve_action(action: str, approved_by: str = "Priyanshu", reason: str = "Manual approval"):
    return get_permission_manager().approve_action(action, approved_by, reason)


def deny_action(action: str, denied_by: str = "Priyanshu", reason: str = "Manual denial"):
    return get_permission_manager().deny_action(action, denied_by, reason)


def set_custom_rule(action: str, level: PermissionLevel):
    get_permission_manager().set_custom_rule(action, level)


def get_pending_approvals():
    return get_permission_manager().get_pending_approvals()


def get_permission_history(limit: int = 50):
    return get_permission_manager().get_permission_history(limit)
