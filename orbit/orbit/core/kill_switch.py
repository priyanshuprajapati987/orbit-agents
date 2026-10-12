# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Kill Switch + Agent Trust System

Global emergency stop for all autonomous actions, dry-run simulation mode,
and per-agent trust levels with daily spend caps.

Safety is not optional — it's the foundation of autonomous operation.
"""

import json
import threading
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from ..logging_config import get_logger
from .runtime_paths import orbit_data_dir, atomic_write_text

logger = get_logger(__name__)

_TRUST_DIR = orbit_data_dir() / "trust"
_TRUST_DIR.mkdir(parents=True, exist_ok=True)


class TrustLevel(Enum):
    LOW = "low"  # read-only, no financial, no external
    MEDIUM = "medium"  # file ops, internal tools
    HIGH = "high"  # most tools, limited financial
    CRITICAL = "critical"  # full access (human equivalent)


@dataclass
class AgentTrustConfig:
    agent_id: str
    trust_level: TrustLevel = TrustLevel.MEDIUM
    daily_budget_usd: float = 5.0
    monthly_budget_usd: float = 100.0
    allowed_tools: List[str] = field(default_factory=list)
    blocked_tools: List[str] = field(default_factory=list)
    max_api_calls_per_day: int = 200
    requires_approval_above: float = 1.0  # dollars


class KillSwitch:
    """Global emergency stop. When activated, ALL autonomous actions halt."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._active = False
        self._activated_at: Optional[str] = None
        self._activated_by: str = ""
        self._reason: str = ""
        self._dry_run = False
        self._dry_run_log: List[Dict] = []
        self._persist_file = _TRUST_DIR / "kill_switch.json"
        self._load()
        self._initialized = True

    def _load(self):
        try:
            if self._persist_file.exists():
                data = json.loads(self._persist_file.read_text(encoding="utf-8"))
                self._active = data.get("active", False)
                self._activated_at = data.get("activated_at")
                self._activated_by = data.get("activated_by", "")
                self._reason = data.get("reason", "")
                self._dry_run = data.get("dry_run", False)
        except Exception:
            pass

    def _save(self):
        try:
            data = {
                "active": self._active,
                "activated_at": self._activated_at,
                "activated_by": self._activated_by,
                "reason": self._reason,
                "dry_run": self._dry_run,
            }
            atomic_write_text(self._persist_file, json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error("Kill switch persist failed: %s", e)

    def activate(self, by: str = "human", reason: str = "emergency") -> Dict:
        """Activate the kill switch — ALL actions stop."""
        with self._lock:
            self._active = True
            self._activated_at = datetime.now().isoformat()
            self._activated_by = by
            self._reason = reason
            self._save()
        logger.critical("KILL SWITCH ACTIVATED by %s: %s", by, reason)
        return {"active": True, "activated_at": self._activated_at, "reason": reason}

    def deactivate(self, by: str = "human") -> Dict:
        """Deactivate the kill switch — resume normal operations."""
        with self._lock:
            self._active = False
            self._activated_at = None
            self._activated_by = ""
            self._reason = ""
            self._save()
        logger.info("Kill switch deactivated by %s", by)
        return {"active": False}

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def status(self) -> Dict:
        return {
            "active": self._active,
            "activated_at": self._activated_at,
            "activated_by": self._activated_by,
            "reason": self._reason,
            "dry_run": self._dry_run,
        }

    # ── Dry-run mode ──────────────────────────────────────────────────

    def enable_dry_run(self) -> Dict:
        """Enable dry-run mode: actions are logged but NOT executed."""
        with self._lock:
            self._dry_run = True
            self._dry_run_log.clear()
            self._save()
        logger.info("Dry-run mode ENABLED")
        return {"dry_run": True}

    def disable_dry_run(self) -> Dict:
        with self._lock:
            self._dry_run = False
            self._dry_run_log.clear()
            self._save()
        logger.info("Dry-run mode DISABLED")
        return {"dry_run": False}

    def is_dry_run(self) -> bool:
        with self._lock:
            return self._dry_run

    def log_dry_run(self, action: str, details: str = "", cost_usd: float = 0.0) -> None:
        """Log what WOULD happen in dry-run mode."""
        with self._lock:
            self._dry_run_log.append(
                {
                    "ts": datetime.now().isoformat(),
                    "action": action,
                    "details": details,
                    "cost_usd": cost_usd,
                }
            )
            if len(self._dry_run_log) > 500:
                self._dry_run_log = self._dry_run_log[-250:]

    def get_dry_run_log(self) -> List[Dict]:
        with self._lock:
            return list(self._dry_run_log)

    def get_dry_run_summary(self) -> Dict:
        with self._lock:
            total_cost = sum(e.get("cost_usd", 0) for e in self._dry_run_log)
            return {
                "dry_run": self._dry_run,
                "pending_actions": len(self._dry_run_log),
                "total_estimated_cost_usd": round(total_cost, 4),
                "actions": self._dry_run_log[-20:],
            }


class AgentTrustManager:
    """Per-agent trust levels and daily spend caps."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._configs: Dict[str, AgentTrustConfig] = {}
        self._daily_usage: Dict[str, Dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "calls": 0})
        self._daily_reset: Dict[str, str] = {}
        self._user_plans: Dict[str, str] = {}
        self._persist_file = _TRUST_DIR / "agent_trust.json"
        self._lock = threading.RLock()
        self._load_defaults()
        self._load()
        self._initialized = True

    def _load_defaults(self):
        """Pre-configure trust for all known agents."""
        defaults = {
            "TAS": (TrustLevel.MEDIUM, 5.0, 100),
            "COD": (TrustLevel.MEDIUM, 5.0, 100),
            "RES": (TrustLevel.LOW, 2.0, 50),
            "WRI": (TrustLevel.MEDIUM, 3.0, 80),
            "COA": (TrustLevel.HIGH, 10.0, 200),
            "DEC": (TrustLevel.MEDIUM, 5.0, 100),
            "SEC": (TrustLevel.HIGH, 0.0, 50),
            "MEM": (TrustLevel.LOW, 0.0, 30),
            "SCR": (TrustLevel.MEDIUM, 2.0, 60),
            "SOC": (TrustLevel.MEDIUM, 3.0, 80),
            "MTG": (TrustLevel.MEDIUM, 5.0, 100),
            "FTN": (TrustLevel.HIGH, 10.0, 50),
            "THB": (TrustLevel.MEDIUM, 3.0, 60),
            "SEO": (TrustLevel.MEDIUM, 3.0, 80),
            "QNT": (TrustLevel.HIGH, 0.0, 20),
            "LOG": (TrustLevel.MEDIUM, 3.0, 80),
            "CNC": (TrustLevel.HIGH, 15.0, 200),
            "PLN": (TrustLevel.MEDIUM, 5.0, 100),
            "EXE": (TrustLevel.HIGH, 10.0, 150),
            "PMA": (TrustLevel.LOW, 0.0, 30),
            "CRT": (TrustLevel.MEDIUM, 3.0, 60),
            "HRM": (TrustLevel.HIGH, 15.0, 100),
            "QUEEN": (TrustLevel.CRITICAL, 50.0, 500),
            # ROUTER = the LLM gateway itself (router.generate safety gate).
            # Uppercase to match _configs/update_config convention; the audit
            # path records usage under this same id so the gate's per-day call
            # limit actually counts real traffic.
            "ROUTER": (TrustLevel.CRITICAL, 50.0, 5000),
        }
        for agent_id, (level, budget, calls) in defaults.items():
            if agent_id not in self._configs:
                self._configs[agent_id] = AgentTrustConfig(
                    agent_id=agent_id,
                    trust_level=level,
                    daily_budget_usd=budget,
                    max_api_calls_per_day=calls,
                )

    def _load(self):
        try:
            if self._persist_file.exists():
                data = json.loads(self._persist_file.read_text(encoding="utf-8"))
                for cfg_data in data.get("configs", []):
                    aid = cfg_data.get("agent_id", "")
                    if aid:
                        self._configs[aid] = AgentTrustConfig(
                            agent_id=aid,
                            trust_level=TrustLevel(cfg_data.get("trust_level", "medium")),
                            daily_budget_usd=cfg_data.get("daily_budget_usd", 5.0),
                            monthly_budget_usd=cfg_data.get("monthly_budget_usd", 100.0),
                            allowed_tools=cfg_data.get("allowed_tools", []),
                            blocked_tools=cfg_data.get("blocked_tools", []),
                            max_api_calls_per_day=cfg_data.get("max_api_calls_per_day", 200),
                            requires_approval_above=cfg_data.get("requires_approval_above", 1.0),
                        )
        except Exception:
            pass

    def _save(self):
        try:
            data = {
                "configs": [asdict(c) for c in self._configs.values()],
                "updated_at": datetime.now().isoformat(),
            }
            # Serialize TrustLevel enums
            for cfg in data["configs"]:
                cfg["trust_level"] = (
                    cfg["trust_level"].value if hasattr(cfg["trust_level"], "value") else cfg["trust_level"]
                )
            atomic_write_text(self._persist_file, json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error("Agent trust persist failed: %s", e)

    def _check_daily_reset(self, agent_id: str):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_reset.get(agent_id) != today:
            self._daily_usage[agent_id] = {"cost": 0.0, "calls": 0}
            self._daily_reset[agent_id] = today

    def can_act(self, agent_id: str, cost_usd: float = 0.0, tool: str = "", user_id: str = "") -> Dict:
        """Check if an agent is allowed to perform an action."""
        # Kill switch check
        ks = KillSwitch()
        if ks.is_active():
            return {"allowed": False, "reason": "kill_switch_active"}

        # Dry-run mode
        if ks.is_dry_run():
            ks.log_dry_run(f"{agent_id}:{tool}", cost_usd=cost_usd)
            return {"allowed": True, "reason": "dry_run_logged", "dry_run": True}

        with self._lock:
            return self._can_act_locked(agent_id, cost_usd, tool, user_id=user_id)

    # Per-user plan caps (Milestone 3 billing): (daily_usd, daily_calls).
    # Solo/legacy callers pass no user_id and keep the agent-level limits.
    USER_PLAN_CAPS = {
        "free": (2.0, 200),
        "pro": (20.0, 2000),
    }

    def set_user_plan(self, user_id: str, plan: str) -> None:
        with self._lock:
            self._user_plans[str(user_id or "")] = (plan or "free").lower()

    def _user_key(self, user_id: str, agent_id: str) -> str:
        user_id = str(user_id or "")
        return f"{user_id}:{agent_id}" if user_id else agent_id

    def _user_cap(self, user_id: str):
        plan = self._user_plans.get(str(user_id or ""), "free")
        return self.USER_PLAN_CAPS.get(plan, self.USER_PLAN_CAPS["free"])

    def try_reserve(self, agent_id: str, cost_usd: float = 0.0, tool: str = "", user_id: str = "") -> Dict:
        """Atomic check-AND-reserve: the budget/call-limit decision and the
        deduction happen under ONE lock acquisition.

        can_act() + record_usage() as separate calls lets N concurrent
        agents all pass against the same stale balance (measured 4x
        overspend). Callers performing real work must use try_reserve and
        call release_reservation() if the work fails.
        """
        with self._lock:
            decision = self._can_act_locked(agent_id, cost_usd, tool, user_id=user_id)
            if not decision.get("allowed"):
                return decision
            key = self._user_key(user_id, agent_id)
            self._check_daily_reset(key)
            usage = self._daily_usage[key]
            usage["cost"] += cost_usd
            usage["calls"] += 1
            decision["reserved_cost"] = cost_usd
            return decision

    def release_reservation(self, agent_id: str, cost_usd: float = 0.0, user_id: str = "") -> None:
        """Refund a reservation when the reserved work fails."""
        with self._lock:
            key = self._user_key(user_id, agent_id)
            self._check_daily_reset(key)
            usage = self._daily_usage[key]
            usage["cost"] = max(0.0, usage["cost"] - cost_usd)
            usage["calls"] = max(0, usage["calls"] - 1)

    def _can_act_locked(self, agent_id: str, cost_usd: float = 0.0, tool: str = "", user_id: str = "") -> Dict:
        """can_act body assuming self._lock is held (no kill-switch check;
        callers check that first — it uses its own lock)."""
        key = self._user_key(user_id, agent_id)
        self._check_daily_reset(key)
        config = self._configs.get(agent_id)
        if config is None:
            return {"allowed": False, "reason": "unknown_agent", "trust_level": "low"}

        usage = self._daily_usage[key]

        # Per-user plan cap (all agents combined). Solo callers skip this.
        if user_id:
            cap_usd, cap_calls = self._user_cap(user_id)
            prefix = f"{user_id}:"
            user_spent = sum(
                u.get("cost", 0.0) for k, u in self._daily_usage.items() if k.startswith(prefix)
            )
            user_calls = sum(
                u.get("calls", 0) for k, u in self._daily_usage.items() if k.startswith(prefix)
            )
            if cost_usd > 0 and user_spent + cost_usd > cap_usd:
                return {
                    "allowed": False,
                    "reason": "plan_quota_exceeded",
                    "spent": round(user_spent, 4),
                    "limit": cap_usd,
                    "upgrade": True,
                }
            if user_calls >= cap_calls:
                return {
                    "allowed": False,
                    "reason": "plan_call_quota_exceeded",
                    "calls": user_calls,
                    "limit": cap_calls,
                    "upgrade": True,
                }

        if cost_usd > 0 and usage["cost"] + cost_usd > config.daily_budget_usd:
            out = {
                "allowed": False,
                "reason": "daily_budget_exceeded",
                "spent": usage["cost"],
                "limit": config.daily_budget_usd,
            }
            if user_id:
                out["upgrade"] = True
            return out

        if usage["calls"] >= config.max_api_calls_per_day:
            return {
                "allowed": False,
                "reason": "daily_call_limit_reached",
                "calls": usage["calls"],
                "limit": config.max_api_calls_per_day,
            }

        if tool and tool in config.blocked_tools:
            return {"allowed": False, "reason": "tool_blocked", "tool": tool}

        if cost_usd > config.requires_approval_above:
            return {
                "allowed": False,
                "reason": "approval_required",
                "cost_usd": cost_usd,
                "threshold": config.requires_approval_above,
            }

        return {"allowed": True, "trust_level": config.trust_level.value}

    def record_usage(self, agent_id: str, cost_usd: float = 0.0, user_id: str = "") -> None:
        """Record API usage for an agent."""
        with self._lock:
            key = self._user_key(user_id, agent_id)
            self._check_daily_reset(key)
            self._daily_usage[key]["cost"] += cost_usd
            self._daily_usage[key]["calls"] += 1

    def get_usage(self, agent_id: str, user_id: str = "") -> Dict:
        with self._lock:
            key = self._user_key(user_id, agent_id)
            self._check_daily_reset(key)
            config = self._configs.get(agent_id)
            usage = self._daily_usage.get(key, {"cost": 0.0, "calls": 0})
            out = {
                "agent_id": agent_id,
                "trust_level": config.trust_level.value if config else "unknown",
                "daily_cost": round(usage["cost"], 4),
                "daily_budget": config.daily_budget_usd if config else 0,
                "daily_calls": usage["calls"],
                "daily_call_limit": config.max_api_calls_per_day if config else 0,
                "cost_remaining": round((config.daily_budget_usd if config else 0) - usage["cost"], 4),
            }
            if user_id:
                cap_usd, cap_calls = self._user_cap(user_id)
                out["plan"] = self._user_plans.get(str(user_id), "free")
                out["plan_daily_usd"] = cap_usd
                out["plan_daily_calls"] = cap_calls
            return out

    def get_all_usage(self) -> List[Dict]:
        with self._lock:
            return [self.get_usage(aid) for aid in sorted(self._configs)]

    # update_config guardrails: without them any API-key holder can
    # promote agents to CRITICAL, set 1e12 budgets, clear blocked_tools,
    # or mint unbounded attacker-chosen agent IDs.
    _UPDATABLE_FIELDS = frozenset(
        {
            "trust_level",
            "daily_budget_usd",
            "monthly_budget_usd",
            "allowed_tools",
            "blocked_tools",
            "max_api_calls_per_day",
            "requires_approval_above",
        }
    )
    _MAX_DAILY_BUDGET = 1000.0
    _MAX_MONTHLY_BUDGET = 20000.0
    _MAX_CALLS_PER_DAY = 100000
    _MAX_APPROVAL_THRESHOLD = 1000.0

    def update_config(self, agent_id: str, **kwargs) -> Dict:
        with self._lock:
            agent_id = str(agent_id or "").strip().upper()
            if not agent_id or len(agent_id) > 32:
                return {"error": "invalid agent_id"}
            config = self._configs.get(agent_id)
            if config is None:
                # No open registration: unknown IDs are rejected instead of
                # creating unbounded entries in _configs/_daily_usage.
                return {"error": f"unknown agent_id: {agent_id}"}
            for k, v in kwargs.items():
                if k not in self._UPDATABLE_FIELDS:
                    continue
                if k == "trust_level":
                    if isinstance(v, str):
                        try:
                            v = TrustLevel(v.lower())
                        except ValueError:
                            return {"error": f"invalid trust_level: {v}"}
                    if not isinstance(v, TrustLevel):
                        return {"error": "invalid trust_level"}
                elif k in ("daily_budget_usd", "monthly_budget_usd", "requires_approval_above"):
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        return {"error": f"invalid {k}: must be numeric"}
                    cap = {
                        "daily_budget_usd": self._MAX_DAILY_BUDGET,
                        "monthly_budget_usd": self._MAX_MONTHLY_BUDGET,
                        "requires_approval_above": self._MAX_APPROVAL_THRESHOLD,
                    }[k]
                    if not (0 <= v <= cap):
                        return {"error": f"{k} out of range 0..{cap}"}
                elif k == "max_api_calls_per_day":
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        return {"error": "invalid max_api_calls_per_day: must be integer"}
                    if not (0 <= v <= self._MAX_CALLS_PER_DAY):
                        return {"error": f"max_api_calls_per_day out of range 0..{self._MAX_CALLS_PER_DAY}"}
                elif k in ("allowed_tools", "blocked_tools"):
                    if isinstance(v, str):
                        v = [v]
                    if not isinstance(v, (list, tuple)) or len(v) > 100:
                        return {"error": f"invalid {k}: list of <=100 tool names"}
                    v = [str(t)[:64] for t in v]
                setattr(config, k, v)
            self._save()
            return asdict(config)


def get_kill_switch() -> KillSwitch:
    return KillSwitch()


def get_trust_manager() -> AgentTrustManager:
    return AgentTrustManager()


def _reset_singletons_for_tests():
    """Reset all singletons for test isolation."""
    KillSwitch._instance = None
    AgentTrustManager._instance = None
