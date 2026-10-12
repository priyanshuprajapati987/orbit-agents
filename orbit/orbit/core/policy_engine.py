# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Central Policy Engine / ToolGate

Single enforcement point for every medium/high-risk tool action:

    Tool Request
        ↓
    Risk Classification
        ↓
    Filesystem Sandbox Check (path traversal / symlink / junction defense)
        ↓
    Permission Check (existing PermissionManager)
        ↓
    Approval Gate (HIGH/CRITICAL in strict mode)
        ↓
    Audit Log (append-only JSONL, secret-redacted)

Modes:
- Default: sandbox enforced for mutating ops; HIGH/CRITICAL audited and
  auto-allowed *inside* the workspace; external destructive paths blocked
  unless explicitly approved via `approve_path`.
- Strict (`ORBIT_STRICT_POLICY=1`): HIGH/CRITICAL actions are blocked
  until approved via `approve_action`.

Human is always the final authority. Agents execute.
"""

from .runtime_paths import app_root
import os
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set

from ..logging_config import get_logger
from .secret_redactor import get_redactor

logger = get_logger(__name__)


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Risk classification per tool action ──────────────────────────────
TOOL_ACTION_RISK: Dict[str, RiskLevel] = {
    # filesystem
    "file.read": RiskLevel.LOW,
    "file.list": RiskLevel.LOW,
    "file.search": RiskLevel.LOW,
    "file.info": RiskLevel.LOW,
    "file.pdf": RiskLevel.LOW,
    "file.write": RiskLevel.MEDIUM,
    "file.edit": RiskLevel.MEDIUM,
    "file.copy": RiskLevel.MEDIUM,
    "file.move": RiskLevel.HIGH,
    "file.delete": RiskLevel.HIGH,
    "file.archive": RiskLevel.HIGH,
    # desktop automation
    "desktop.screenshot": RiskLevel.LOW,
    "desktop.screen_size": RiskLevel.LOW,
    "desktop.pixel_color": RiskLevel.LOW,
    "desktop.mouse_position": RiskLevel.LOW,
    "desktop.clipboard_read": RiskLevel.LOW,
    "desktop.list_windows": RiskLevel.LOW,
    "desktop.list_running_apps": RiskLevel.LOW,
    "desktop.mouse_move": RiskLevel.LOW,
    "desktop.mouse_click": RiskLevel.MEDIUM,
    "desktop.mouse_double_click": RiskLevel.MEDIUM,
    "desktop.mouse_right_click": RiskLevel.MEDIUM,
    "desktop.mouse_drag": RiskLevel.MEDIUM,
    "desktop.mouse_scroll": RiskLevel.MEDIUM,
    "desktop.type_text": RiskLevel.MEDIUM,
    "desktop.type_unicode": RiskLevel.MEDIUM,
    "desktop.press_key": RiskLevel.MEDIUM,
    "desktop.hotkey": RiskLevel.MEDIUM,
    "desktop.key_down": RiskLevel.MEDIUM,
    "desktop.key_up": RiskLevel.MEDIUM,
    "desktop.clipboard_write": RiskLevel.MEDIUM,
    "desktop.focus_window": RiskLevel.MEDIUM,
    "desktop.minimize_window": RiskLevel.MEDIUM,
    "desktop.maximize_window": RiskLevel.MEDIUM,
    "desktop.move_window": RiskLevel.MEDIUM,
    "desktop.open_app": RiskLevel.MEDIUM,
    "desktop.open_url": RiskLevel.MEDIUM,
    "desktop.launch_explorer": RiskLevel.MEDIUM,
    "desktop.close_window": RiskLevel.HIGH,
    "desktop.ps": RiskLevel.HIGH,
    "desktop.process_kill": RiskLevel.HIGH,
    # github
    "github.list": RiskLevel.LOW,
    "github.read": RiskLevel.LOW,
    "github.search": RiskLevel.LOW,
    "github.git_status": RiskLevel.LOW,
    "github.git_log": RiskLevel.LOW,
    "github.git_diff": RiskLevel.LOW,
    "github.create_issue": RiskLevel.MEDIUM,
    "github.comment": RiskLevel.MEDIUM,
    "github.add_label": RiskLevel.MEDIUM,
    "github.create_branch": RiskLevel.MEDIUM,
    "github.create_pr": RiskLevel.MEDIUM,
    "github.trigger_workflow": RiskLevel.MEDIUM,
    "github.git_add": RiskLevel.MEDIUM,
    "github.git_commit": RiskLevel.MEDIUM,
    "github.review_pr": RiskLevel.MEDIUM,
    "github.close_issue": RiskLevel.HIGH,
    "github.git_push": RiskLevel.HIGH,
    "github.create_repo": RiskLevel.HIGH,
    "github.fork_repo": RiskLevel.HIGH,
    "github.merge_pr": RiskLevel.CRITICAL,
    "github.delete_branch": RiskLevel.CRITICAL,
    "github.delete_repo": RiskLevel.CRITICAL,
    # system / computer
    "system.info": RiskLevel.LOW,
    "system.process_list": RiskLevel.LOW,
    "system.clipboard_read": RiskLevel.LOW,
    "system.command": RiskLevel.HIGH,
    "system.process_kill": RiskLevel.HIGH,
    "system.firewall_toggle": RiskLevel.CRITICAL,
    "system.install": RiskLevel.HIGH,
    "system.config_change": RiskLevel.HIGH,
    "system.security_change": RiskLevel.CRITICAL,
    # comms / financial
    "email.send": RiskLevel.MEDIUM,
    "email.bulk": RiskLevel.HIGH,
    "social.post": RiskLevel.MEDIUM,
    "financial.transaction": RiskLevel.CRITICAL,
}

# Blocked regardless of sandbox (mutating ops)
ALWAYS_BLOCKED_PARTS = {"windows", "system32", "syswow64", "boot", "$recycle.bin"}


def get_workspace_root() -> Path:
    env = os.environ.get("ORBIT_WORKSPACE", "").strip()
    if env:
        return Path(env).resolve()
    return app_root().resolve()


def get_extra_workspaces() -> List[Path]:
    env = os.environ.get("ORBIT_EXTRA_WORKSPACES", "").strip()
    if not env:
        return []
    return [Path(p.strip()).resolve() for p in env.split(";") if p.strip()]


class PolicyDecision:
    __slots__ = ("allowed", "risk", "action", "reason", "audit_id", "approved_by", "details")

    def __init__(
        self,
        allowed: bool,
        risk: RiskLevel,
        action: str,
        reason: str = "",
        audit_id: str = "",
        approved_by: str = "",
        details: str = "",
    ):
        self.allowed = allowed
        self.risk = risk
        self.action = action
        self.reason = reason
        self.audit_id = audit_id
        self.approved_by = approved_by
        self.details = details

    def to_dict(self) -> Dict:
        return {
            "allowed": self.allowed,
            "risk": self.risk.value,
            "action": self.action,
            "reason": self.reason,
            "audit_id": self.audit_id,
            "approved_by": self.approved_by,
            "details": self.details,
        }


class PolicyEngine:
    def __init__(self, workspace: Optional[str] = None, audit_dir: Optional[str] = None):
        _ws = workspace or os.environ.get("ORBIT_WORKSPACE", "")
        self.workspace = Path(_ws).resolve() if _ws else app_root().resolve()
        self.extra_workspaces: List[Path] = get_extra_workspaces()
        self.approved_paths: Set[Path] = set()
        self.approved_actions: Set[str] = set()
        self.strict = os.environ.get("ORBIT_STRICT_POLICY", "1").strip().lower() in {"1", "true", "yes", "on"}
        audit_dir_env = os.environ.get("ORBIT_AUDIT_DIR", "").strip()
        audit_base = (
            Path(audit_dir)
            if audit_dir
            else (Path(audit_dir_env) if audit_dir_env else self.workspace / "orbit_data" / "security")
        )
        self._audit_file = audit_base / "audit.jsonl"
        self._audit_file.parent.mkdir(parents=True, exist_ok=True)
        self._approvals_file = audit_base / "approvals.json"
        self._redactor = get_redactor()
        self._lock = threading.RLock()
        self._last_prune_time = 0.0
        self._load_approvals()

    # ── sandbox ──────────────────────────────────────────────────────
    def _resolve_real(self, path: str) -> Optional[Path]:
        """Resolve path defeating symlinks/junctions via os.path.realpath."""
        try:
            return Path(os.path.realpath(path)).resolve()
        except Exception:
            return None

    def is_in_workspace(self, real: Path) -> bool:
        try:
            if self.workspace and real == self.workspace:
                return True
            if self.workspace and self.workspace in real.parents:
                return True
            for w in self.extra_workspaces:
                if real == w or w in real.parents:
                    return True
            return False
        except Exception:
            return False

    def _has_blocked_part(self, real: Path) -> bool:
        try:
            parts = {p.lower() for p in real.parts}
            return bool(parts & ALWAYS_BLOCKED_PARTS)
        except Exception:
            return False

    def check_path(self, path: str, mutate: bool) -> PolicyDecision:
        """Sandbox check for a filesystem path.

        mutate=False (reads): only hard-blocked system dirs rejected.
        mutate=True (write/delete/move/copy): must be inside workspace or
        an explicitly approved path; system dirs always rejected.
        """
        action = "path.mutate" if mutate else "path.read"
        real = self._resolve_real(path)
        if real is None:
            return PolicyDecision(False, RiskLevel.MEDIUM, action, reason="path_resolve_failed")
        if self._has_blocked_part(real):
            return PolicyDecision(False, RiskLevel.HIGH, action, reason="blocked_system_directory")
        if not mutate:
            return PolicyDecision(True, RiskLevel.LOW, action, reason="read_allowed")
        if self.is_in_workspace(real):
            return PolicyDecision(True, RiskLevel.MEDIUM, action, reason="inside_workspace")
        with self._lock:
            for ap in self.approved_paths:
                if real == ap or ap in real.parents:
                    return PolicyDecision(
                        True, RiskLevel.MEDIUM, action, reason="approved_external_path", approved_by="explicit"
                    )
        return PolicyDecision(False, RiskLevel.HIGH, action, reason="outside_workspace_requires_approval")

    def _load_approvals(self):
        """Load persisted approvals from disk."""
        if self._approvals_file.exists():
            try:
                import json

                with open(self._approvals_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for p in data.get("paths", []):
                    resolved = Path(p)
                    if resolved.exists():
                        self.approved_paths.add(resolved)
                for a in data.get("actions", []):
                    self.approved_actions.add(a)
            except Exception:
                pass  # Corrupted file — start fresh

    def _save_approvals(self):
        """Persist approvals to disk (atomic: a torn approvals file would
        silently reset the ToolGate allowlist on next load)."""
        try:
            import json

            from .runtime_paths import atomic_write_text

            data = {
                "paths": [str(p) for p in self.approved_paths],
                "actions": list(self.approved_actions),
            }
            atomic_write_text(self._approvals_file, json.dumps(data, indent=2))
        except Exception:
            pass

    def approve_path(self, path: str) -> bool:
        real = self._resolve_real(path)
        if real is None:
            return False
        # Never allow approving a filesystem root / drive root: it would
        # whitelist the entire filesystem via the `ap in real.parents` rule
        # in check_path (sandbox kill). Same for known system directories.
        if real.parent == real or self._has_blocked_part(real):
            return False
        with self._lock:
            self.approved_paths.add(real)
            self._save_approvals()
        return True

    def deny_path(self, path: str) -> bool:
        real = self._resolve_real(path)
        if real is None:
            return False
        with self._lock:
            self.approved_paths.discard(real)
            self._save_approvals()
        return True

    # ── permission + approval ─────────────────────────────────────────
    def approve_action(self, action: str) -> None:
        with self._lock:
            self.approved_actions.add(action)
            self._save_approvals()

    def revoke_action(self, action: str) -> None:
        with self._lock:
            self.approved_actions.discard(action)
            self._save_approvals()

    def is_strict(self) -> bool:
        return self.strict

    # ── main gate ─────────────────────────────────────────────────────
    def check(
        self, tool: str, action: str, path: Optional[str] = None, details: str = "", mutate: bool = False
    ) -> PolicyDecision:
        key = f"{tool}.{action}"
        risk = TOOL_ACTION_RISK.get(key, RiskLevel.MEDIUM)

        # Kill switch: block ALL actions when active
        from .kill_switch import get_kill_switch

        ks = get_kill_switch()
        if ks.is_active():
            return self._finalize(
                PolicyDecision(
                    False,
                    risk,
                    key,
                    reason="kill_switch_active",
                    details="All autonomous actions halted by kill switch",
                )
            )

        # Dry-run mode: log but allow (caller should check is_dry_run())
        if ks.is_dry_run():
            ks.log_dry_run(key, details=details)

        if path is not None:
            path_check = self.check_path(path, mutate)
            if not path_check.allowed:
                _RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
                combined_risk = risk if _RISK_ORDER[risk] >= _RISK_ORDER[path_check.risk] else path_check.risk
                return self._finalize(
                    PolicyDecision(
                        False,
                        combined_risk,
                        key,
                        reason=path_check.reason,
                        details=details,
                    )
                )

        # LOW: auto-allow
        if risk == RiskLevel.LOW:
            return self._finalize(PolicyDecision(True, risk, key, reason="low_risk_auto", details=details))

        # MEDIUM: allowed + audited
        if risk == RiskLevel.MEDIUM:
            return self._finalize(PolicyDecision(True, risk, key, reason="medium_risk_audited", details=details))

        # HIGH/CRITICAL: strict mode blocks unless explicitly approved;
        # default mode allows inside-sandbox ops with full audit.
        # Dry-run mode bypasses strict block (logs but allows).
        if self.strict and key not in self.approved_actions and not ks.is_dry_run():
            # Path-scoped actions that already passed check_path() above are
            # sandbox-validated (inside workspace / explicitly approved path /
            # never a system dir) — the sandbox IS the approval boundary for
            # filesystem ops, so allow with full audit. Path-less HIGH actions
            # (desktop.ps, process_kill, git_push, ...) keep requiring approval.
            if path is not None and path_check.allowed:
                return self._finalize(
                    PolicyDecision(True, risk, key, reason="sandbox_validated_high_risk", details=details)
                )
            return self._finalize(
                PolicyDecision(False, risk, key, reason="strict_mode_requires_approval", details=details)
            )

        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return self._finalize(
                PolicyDecision(
                    True,
                    risk,
                    key,
                    reason="approved" if key in self.approved_actions else "audited_high_risk",
                    approved_by="explicit" if key in self.approved_actions else "policy",
                    details=details,
                )
            )

        return self._finalize(PolicyDecision(True, risk, key, reason="default_allow", details=details))

    def _finalize(self, decision: PolicyDecision) -> PolicyDecision:
        decision.audit_id = self._audit(decision)
        # ── P2: tool span tracking ────────────────────────────────────
        try:
            from .execution_trace import get_execution_tracer, _current_span_id

            _tracer = get_execution_tracer()
            # Find any active trace and add a tool span
            for tid, tr in _tracer._active.items():
                parent = _current_span_id.get() or ""
                sid = _tracer.start_span(
                    tid,
                    f"tool:{decision.action}",
                    "tool",
                    parent_id=parent,
                    tool_name=decision.action,
                    input_preview=decision.details[:200] if decision.details else "",
                )
                _tracer.end_span(
                    tid,
                    sid,
                    status="ok" if decision.allowed else "error",
                    error="" if decision.allowed else decision.reason,
                    metadata={"risk": decision.risk.value, "key": decision.action},
                )
                break  # only one active trace per request
        except Exception:
            pass
        # ── P3: loop detector — tool call loop detection ───────────────
        try:
            from .loop_detector import get_loop_detector

            _trace_id = None
            try:
                from .execution_trace import get_execution_tracer

                for tid in get_execution_tracer()._active:
                    _trace_id = tid
                    break
            except Exception:
                pass
            get_loop_detector().record_tool_call(
                decision.action.split(".")[0] if "." in decision.action else "tool", decision.action, _trace_id or ""
            )
        except Exception:
            pass
        # ── end tool span ─────────────────────────────────────────────
        return decision

    # ── audit (append-only JSONL, secret-redacted, daily rotation) ────
    def _audit(self, decision: PolicyDecision) -> str:
        import uuid

        audit_id = uuid.uuid4().hex[:12]
        entry = {
            "audit_id": audit_id,
            "ts": datetime.now().isoformat(),
            "action": decision.action,
            "risk": decision.risk.value,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "approved_by": decision.approved_by,
            "details": decision.details or "",
        }
        try:
            redacted = self._redactor.redact_dict(entry)
            # Daily rotation: audit_YYYYMMDD.jsonl
            day_str = datetime.now().strftime("%Y%m%d")
            audit_dir = self._audit_file.parent
            daily_file = audit_dir / f"audit_{day_str}.jsonl"
            with open(daily_file, "a", encoding="utf-8") as f:
                f.write(__import__("json").dumps(redacted, ensure_ascii=False) + "\n")
            # Also keep symlink/compat with main file
            with open(self._audit_file, "a", encoding="utf-8") as f:
                f.write(__import__("json").dumps(redacted, ensure_ascii=False) + "\n")
            # Prune audit files older than 30 days (throttled: once per hour)
            now = time.time()
            if now - self._last_prune_time > 3600:
                self._prune_audit_logs(audit_dir)
                self._last_prune_time = now
        except Exception as e:
            logger.error("Audit write failed: %s", e)
        return audit_id

    def _prune_audit_logs(self, audit_dir) -> None:
        """Remove audit JSONL files older than 30 days."""
        try:
            from datetime import timedelta

            cutoff = datetime.now() - timedelta(days=30)
            for f in audit_dir.glob("audit_*.jsonl"):
                if f.name == "audit.jsonl":
                    continue  # skip main compat file
                try:
                    date_str = f.stem.replace("audit_", "")
                    file_date = datetime.strptime(date_str, "%Y%m%d")
                    if file_date < cutoff:
                        f.unlink()
                except (ValueError, OSError):
                    pass
        except Exception:
            pass

    def get_audit_log(self, limit: int = 50) -> List[Dict]:
        out: List[Dict] = []
        if not self._audit_file.exists():
            return out
        try:
            with open(self._audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(self._redactor.redact_dict(__import__("json").loads(line)))
                    except Exception:
                        continue
        except Exception:
            return []
        return out[-limit:]

    def stats(self) -> Dict:
        return {
            "workspace": str(self.workspace),
            "strict": self.strict,
            "approved_paths": len(self.approved_paths),
            "approved_actions": sorted(self.approved_actions),
            "extra_workspaces": [str(w) for w in self.extra_workspaces],
        }


_engine: Optional[PolicyEngine] = None
_engine_lock = threading.Lock()


def get_policy_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = PolicyEngine()
    return _engine


def check_tool(
    tool: str, action: str, path: Optional[str] = None, details: str = "", mutate: bool = False
) -> PolicyDecision:
    return get_policy_engine().check(tool, action, path=path, details=details, mutate=mutate)


def approve_path(path: str) -> bool:
    return get_policy_engine().approve_path(path)


def get_audit_log(limit: int = 50) -> List[Dict]:
    return get_policy_engine().get_audit_log(limit)
