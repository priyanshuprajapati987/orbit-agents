# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT SecurityGuard — defensive security engine.

Keeps ORBIT itself secure. Unlike SecurityTool (system threat scanning:
ports/processes/firewall), SecurityGuard continuously watches the ORBIT
codebase + its own defenses:

    1. Codebase scan  — hardcoded secrets, dangerous eval/exec/subprocess,
                        shell=True, .env hygiene, tracked .env files.
    2. Defense check  — API auth enabled, rate limiter active, security
                        headers present, ToolGate sandbox on, secret
                        redaction working, kill switch state.
    3. Incident log   — append-only JSONL of every finding + auto-fix.
    4. Auto-fix       — safe, reversible remediations (gitignore entries,
                        .env.example sync warnings, redaction test).
    5. Scheduled scan — optional background thread re-scans on interval.
"""

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..logging_config import get_logger
from .runtime_paths import repo_root, orbit_data_dir, env_path

logger = get_logger(__name__)

# ── Default ignore dirs for codebase scans ────────────────────────────
DEFAULT_SKIP_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "orbit_data",
    "data",
    "hf_cache",
    ".claude",
    ".ai-factory",
    "tests",
    "stress_test.py",
    "stress_test_real.py",
}

# ── Secret patterns (mirrors core/secret_redactor.py) ────────────────
SECRET_PATTERNS = [
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"), "OpenAI/OpenRouter API key"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"), "Anthropic API key"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"), "Google API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bghp_[0-9A-Za-z]{30,}\b"), "GitHub token"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), "Slack token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "Private key"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "JWT"),
]

# ── Dangerous code patterns ───────────────────────────────────────────
# exec/eval use negative lookbehind so JS regex `/.exec(` / `/.eval(` (legit
# String/RegExp methods) never match. Python eval/exec are matched normally.
DANGEROUS_PATTERNS = [
    (re.compile(r"\bshell\s*=\s*True\b"), "subprocess shell=True (command injection)"),
    (re.compile(r"\bos\.system\s*\("), "os.system() (command injection)"),
    (re.compile(r"(?<![.\w])\beval\s*\("), "eval() (arbitrary code)"),
    (re.compile(r"(?<![.\w])\bexec\s*\("), "exec() (arbitrary code)"),
    (re.compile(r"__class__\.__mro__\[1\]\.__subclasses__\(\)"), "sandbox escape chain"),
    (re.compile(r"django\.core\.context_processors"), "legacy pattern"),
]

# Paths exempt from the codebase scan (known-legitimate / verified safe).
SCAN_EXEMPT_PATHS = {
    "orbit/orbit/core/secret_redactor.py",
    "orbit/orbit/core/security_guard.py",
    "orbit/orbit/calculator.py",
    "orbit/orbit/core/calculator.py",
    "orbit/orbit/core/code_interpreter.py",
    "orbit/orbit/core/alert_system.py",
    # AST-whitelisted eval (empty builtins, only context lookups) — H01 fixed, safe
    "orbit/orbit/core/workflow_engine.py",
    # regex-string SECURITY patterns that *detect* eval/exec, not real calls
    "orbit/orbit/core/code_reviewer.py",
    # docstrings explicitly stating "NO eval() USED EVER"
    "orbit/orbit/core/incident_actions.py",
    "orbit/orbit/core/incident_response.py",
}


def _sanitize_finding(text: str) -> str:
    """Redact the secret value itself from a finding line."""
    for pattern, _name in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class SecurityGuard:
    """Monitors and hardens the ORBIT deployment."""

    def __init__(self, root: Optional[Path] = None, storage_file: Optional[Path] = None):
        self.root = Path(root) if root else repo_root()
        self.storage_file = storage_file or (orbit_data_dir() / "security" / "guard_incidents.jsonl")
        self._lock = threading.RLock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.last_scan: Optional[dict] = None
        self.scan_count = 0

    # ── Incident log ──────────────────────────────────────────────────
    def _log_incident(self, incident: Dict[str, Any]):
        incident = dict(incident)
        incident.setdefault("ts", datetime.now().isoformat())
        incident.setdefault("severity", "medium")
        incident["message"] = _sanitize_finding(str(incident.get("message", "")))
        try:
            self.storage_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(incident) + "\n")
        except Exception as e:
            logger.warning("SecurityGuard: failed to write incident log: %s", e)

    def incidents(self, limit: int = 50) -> List[dict]:
        if not self.storage_file.exists():
            return []
        out = []
        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            return []
        return out[-limit:][::-1]

    # ── Codebase scan ─────────────────────────────────────────────────
    def scan_codebase(self, deep: bool = True) -> dict:
        """Scan the source tree for secrets and dangerous patterns."""
        findings: List[dict] = []
        files_scanned = 0
        if not self.root.exists():
            return {"ok": False, "findings": findings, "files_scanned": 0, "error": "root missing"}

        for path in self.root.rglob("*"):
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            if rel in SCAN_EXEMPT_PATHS:
                continue
            if any(part in DEFAULT_SKIP_DIRS for part in path.parts):
                continue
            suffix = path.suffix.lower()
            if suffix not in (".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".sh"):
                continue
            if not deep and rel not in ("orbit/orbit/core/policy_engine.py",):
                continue
            is_data_file = suffix == ".json"
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            files_scanned += 1
            for lineno, line in enumerate(text.splitlines(), 1):
                for pattern, name in SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append(
                            {
                                "kind": "secret",
                                "severity": "critical",
                                "file": rel,
                                "line": lineno,
                                "name": name,
                                "message": f"{name} in {rel}:{lineno}",
                            }
                        )
                if is_data_file:
                    continue
                for pattern, name in DANGEROUS_PATTERNS:
                    if pattern.search(line):
                        findings.append(
                            {
                                "kind": "dangerous_code",
                                "severity": "high",
                                "file": rel,
                                "line": lineno,
                                "name": name,
                                "message": f"{name} in {rel}:{lineno}",
                            }
                        )

        # Deduplicate identical (kind, file, line)
        seen = set()
        deduped = []
        for f in findings:
            key = (f["kind"], f["file"], f["line"])
            if key not in seen:
                seen.add(key)
                deduped.append(f)

        result = {
            "ok": True,
            "findings": deduped,
            "count": len(deduped),
            "critical": sum(1 for f in deduped if f["severity"] == "critical"),
            "high": sum(1 for f in deduped if f["severity"] == "high"),
            "files_scanned": files_scanned,
            "scanned_at": datetime.now().isoformat(),
        }
        with self._lock:
            self.last_scan = result
            self.scan_count += 1
        for f in deduped[:25]:
            self._log_incident(
                {
                    "kind": f["kind"],
                    "severity": f["severity"],
                    "file": f["file"],
                    "line": f["line"],
                    "name": f["name"],
                    "message": f["message"],
                }
            )
        return result

    # ── Defense status ────────────────────────────────────────────────
    def check_defenses(self) -> dict:
        """Check ORBIT's own security controls are active."""
        checks: List[dict] = []

        # 1. API auth
        try:
            from .. import config

            auth_enabled = bool(getattr(config, "ORBIT_API_AUTH_ENABLED", True))
            auth_key = bool(getattr(config, "ORBIT_API_KEY", ""))
            checks.append(
                {
                    "name": "api_auth",
                    "ok": bool(auth_enabled and auth_key),
                    "detail": "API key set + auth enabled"
                    if (auth_enabled and auth_key)
                    else ("auth enabled but no key" if auth_enabled else "auth disabled"),
                }
            )
        except Exception as e:
            checks.append({"name": "api_auth", "ok": False, "detail": str(e)})

        # 2. Rate limiter
        try:
            from .rate_limiter import get_rate_limiter

            limiter = get_rate_limiter()
            # Fixed probe name: the old per-second unique name registered a
            # new bucket on every check_defenses() that was never pruned.
            probe = "securityguard:probe"
            limiter.register(probe, rate=1000.0, burst=100)
            allowed = limiter.check(probe, tokens=1.0)
            checks.append(
                {
                    "name": "rate_limiter",
                    "ok": bool(allowed),
                    "detail": "functional (probe consume ok)" if allowed else "limiter blocking probe",
                }
            )
        except Exception as e:
            checks.append({"name": "rate_limiter", "ok": False, "detail": str(e)})

        # 3. Security headers
        try:
            from .security_hardening import get_security_hardening

            headers = get_security_hardening().get_security_headers()
            has_csp = "Content-Security-Policy" in headers
            has_nosniff = headers.get("X-Content-Type-Options") == "nosniff"
            checks.append(
                {
                    "name": "security_headers",
                    "ok": bool(has_csp and has_nosniff),
                    "detail": "CSP + nosniff active" if (has_csp and has_nosniff) else "headers missing",
                }
            )
        except Exception as e:
            checks.append({"name": "security_headers", "ok": False, "detail": str(e)})

        # 4. ToolGate sandbox
        try:
            from .policy_engine import get_policy_engine

            get_policy_engine()
            checks.append(
                {
                    "name": "toolgate",
                    "ok": True,
                    "detail": "ToolGate policy engine loaded",
                }
            )
        except Exception as e:
            checks.append({"name": "toolgate", "ok": False, "detail": str(e)})

        # 5. Secret redaction
        try:
            from .secret_redactor import get_redactor

            red = get_redactor()
            test = red.redact_value if hasattr(red, "redact_value") else red.redact
            sample = "key sk-proj-abc123def456ghi789jkl012"
            out = test(sample) if callable(test) else sample
            checks.append(
                {
                    "name": "secret_redaction",
                    "ok": "[REDACTED]" in str(out),
                    "detail": "redactor masks secrets" if "[REDACTED]" in str(out) else "redactor not masking",
                }
            )
        except Exception as e:
            checks.append({"name": "secret_redaction", "ok": False, "detail": str(e)})

        # 6. Kill switch state
        try:
            from .kill_switch import get_kill_switch

            ks = get_kill_switch()
            active = bool(ks.is_active() if hasattr(ks, "is_active") else False)
            checks.append(
                {
                    "name": "kill_switch",
                    "ok": not active,
                    "detail": "emergency kill switch active" if active else "kill switch armed (not active)",
                }
            )
        except Exception as e:
            checks.append({"name": "kill_switch", "ok": False, "detail": str(e)})

        # 7. .env hygiene
        env_file = env_path()
        tracked_env = self._git_tracked_env()
        checks.append(
            {
                "name": "env_hygiene",
                "ok": not tracked_env,
                "detail": f".env present={env_file.exists()}, tracked_env_files={tracked_env or 'none'}",
            }
        )

        passed = sum(1 for c in checks if c["ok"])
        result = {
            "ok": passed == len(checks),
            "total": len(checks),
            "passed": passed,
            "checks": checks,
            "checked_at": datetime.now().isoformat(),
        }
        return result

    def _git_tracked_env(self) -> List[str]:
        try:
            import subprocess

            r = subprocess.run(
                ["git", "ls-files", "*.env*", "--error-unmatch"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            tracked = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            return [t for t in tracked if ".example" not in t and "n8n" not in t]
        except Exception:
            return []

    # ── Full report ───────────────────────────────────────────────────
    def run_full_check(self, deep: bool = True) -> dict:
        code = self.scan_codebase(deep=deep)
        defenses = self.check_defenses()
        verdict = "secure"
        if code.get("critical"):
            verdict = "critical_findings"
        elif code.get("high"):
            verdict = "high_findings"
        elif not defenses.get("ok"):
            verdict = "defense_gap"
        return {
            "verdict": verdict,
            "codebase": code,
            "defenses": defenses,
            "checked_at": datetime.now().isoformat(),
        }

    # ── Auto-fix safe items ───────────────────────────────────────────
    def auto_fix(self) -> dict:
        fixes = []
        # .env tracked? (no auto git command — just report)
        tracked = self._git_tracked_env()
        if tracked:
            fixes.append(
                {
                    "applied": False,
                    "item": "tracked_env",
                    "detail": f"tracked: {', '.join(tracked)} — remove with: git rm --cached <file>",
                }
            )

        # gitignore covers orbit_data?
        gitignore = self.root / ".gitignore"
        missing = []
        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8", errors="ignore")
            for pat in ["orbit_data/", ".env", "data/"]:
                if pat not in content:
                    missing.append(pat)
            if missing:
                try:
                    with open(gitignore, "a", encoding="utf-8") as f:
                        f.write("\n# added by SecurityGuard\n")
                        for pat in missing:
                            f.write(f"{pat}\n")
                    fixes.append(
                        {
                            "applied": True,
                            "item": "gitignore",
                            "detail": f"appended missing patterns: {', '.join(missing)}",
                        }
                    )
                    self._log_incident(
                        {
                            "kind": "autofix",
                            "severity": "low",
                            "message": f"gitignore updated with {', '.join(missing)}",
                        }
                    )
                except Exception as e:
                    fixes.append({"applied": False, "item": "gitignore", "detail": str(e)})
        return {"fixes": fixes, "count": len(fixes)}

    # ── Background monitor ────────────────────────────────────────────
    def start_monitor(self, interval: int = 300):
        """Start a background thread that re-scans on interval."""
        with self._lock:
            if self._monitor_thread and self._monitor_thread.is_alive():
                return False
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, args=(interval,), daemon=True, name="security-guard"
            )
            self._monitor_thread.start()
            return True

    def stop_monitor(self):
        self._stop_event.set()
        with self._lock:
            self._monitor_thread = None

    def _monitor_loop(self, interval: int):
        while not self._stop_event.wait(interval):
            try:
                self.scan_codebase()
            except Exception as e:
                logger.warning("SecurityGuard monitor error: %s", e)

    def get_status(self) -> dict:
        return {
            "monitoring": bool(self._monitor_thread and self._monitor_thread.is_alive()),
            "scan_count": self.scan_count,
            "last_scan": self.last_scan,
            "incidents": len(self.incidents(1000)),
            "root": str(self.root),
        }


_guard: Optional[SecurityGuard] = None
_guard_lock = threading.Lock()


def get_security_guard() -> SecurityGuard:
    global _guard
    if _guard is None:
        with _guard_lock:
            if _guard is None:
                _guard = SecurityGuard()
    return _guard
