# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import json
import logging
import platform
import re
import subprocess

import psutil

logger = logging.getLogger(__name__)


class SecurityTool:
    def __init__(self):
        self.os = platform.system()

    @staticmethod
    def _ps_quote(s):
        s = str(s)
        # In PowerShell single-quoted strings, only ' needs escaping ('' → literal ')
        # $, `, (, ) are all literal — escaping them corrupts the string
        s = s.replace("'", "''")
        return "'" + s + "'"

    def _ps(self, cmd: str) -> str:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return r.stdout.strip()[:2000] or r.stderr.strip()[:500]
        except Exception as e:
            return str(e)

    def scan_processes(self) -> dict:
        processes = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                processes.append(proc.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        processes.sort(key=lambda p: p.get("cpu_percent", 0) or 0, reverse=True)
        return {"total": len(processes), "top_cpu": processes[:10]}

    def kill_process(self, name: str) -> str:
        # ToolGate check for dangerous operation
        try:
            from ..core.policy_engine import get_policy_engine

            decision = get_policy_engine().check("system", "process_kill", path=None, mutate=True)
            if not decision.allowed:
                return f"❌ Blocked by security policy: {decision.reason}"
        except Exception as e:
            logger.warning("ToolGate check failed for kill_process: %s", e)
            # Fail-closed: block if ToolGate is broken
            return f"❌ Security check failed, operation blocked: {e}"
        killed = []
        # Safety: skip critical system processes
        SAFE_TO_KILL = {
            "chrome",
            "firefox",
            "msedge",
            "notepad",
            "code",
            "spotify",
            "discord",
            "slack",
            "zoom",
            "teams",
        }
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                proc_name = proc.info["name"].lower()
                base_name = proc_name.replace(".exe", "")
                # Exact base-name match only: substring matching lets a
                # single char like "e" match (and kill) chrome.exe.
                if base_name == name.lower().strip().removesuffix(".exe") and base_name in SAFE_TO_KILL:
                    proc.kill()
                    killed.append(f"{proc.info['name']} (PID: {proc.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if killed:
            return f"Killed: {', '.join(killed)}"
        return f"No safe process found matching '{name}' (system processes protected)"

    def scan_ports(self) -> list:
        out = self._ps("Get-NetTCPConnection -State Listen | Select-Object LocalPort,OwningProcess | ConvertTo-Json")
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            return [f"Port {d['LocalPort']} (PID: {d['OwningProcess']})" for d in data[:20]]
        except Exception:
            return ["Could not scan ports"]

    def scan_network(self) -> str:
        return self._ps(
            "Get-NetIPConfiguration | Select-Object InterfaceAlias,IPv4Address,IPv4DefaultGateway | ConvertTo-Json"
        )

    def firewall_status(self) -> str:
        out = self._ps("Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json")
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            return "\n".join([f"{'[ON]' if d['Enabled'] else '[OFF]'} {d['Name']}" for d in data])
        except Exception:
            return "Firewall info unavailable"

    def firewall_toggle(self, enable: bool) -> str:
        # ToolGate check for dangerous operation
        try:
            from ..core.policy_engine import get_policy_engine

            decision = get_policy_engine().check("system", "firewall_toggle", path=None, mutate=True)
            if not decision.allowed:
                return f"❌ Blocked by security policy: {decision.reason}"
        except Exception as e:
            logger.warning("ToolGate check failed for firewall_toggle: %s", e)
            return f"❌ Security check failed, operation blocked: {e}"
        state = "True" if enable else "False"
        return self._ps(f"Set-NetFirewallProfile -All -Enabled {state}")

    def disk_usage(self) -> list:
        drives = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                drives.append(
                    {
                        "device": part.device,
                        "mount": part.mountpoint,
                        "total_gb": round(usage.total / (1024**3), 1),
                        "used_gb": round(usage.used / (1024**3), 1),
                        "free_gb": round(usage.free / (1024**3), 1),
                        "percent": usage.percent,
                    }
                )
            except (PermissionError, OSError):
                continue
        return drives

    def service_control(self, name: str, action: str = "status") -> str:
        valid = {"start", "stop", "restart", "status"}
        if action not in valid:
            return "Invalid action. Use: start, stop, restart, status"
        safe_name = self._ps_quote(name)
        if action == "status":
            return self._ps(f"Get-Service -Name {safe_name} | Select-Object Name,Status,StartType | ConvertTo-Json")
        return self._ps(f"{action}-Service -Name {safe_name} -Force")

    def defender_scan(self) -> str:
        return self._ps(
            "Start-MpScan -ScanType QuickScan; Get-MpThreat | Select-Object Name,Severity,Category | ConvertTo-Json"
        )

    def active_connections(self) -> str:
        return self._ps(
            "Get-NetTCPConnection -State Established | Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,OwningProcess | ConvertTo-Json"
        )

    def full_report(self) -> str:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        lines = [
            "🔒 SYSTEM SECURITY REPORT",
            f"CPU: {cpu}% | RAM: {mem.percent}% ({round(mem.used / 1e9, 1)}/{round(mem.total / 1e9, 1)} GB)",
        ]
        disks = self.disk_usage()
        for d in disks[:3]:
            lines.append(f"{d['device']}: {d['used_gb']}/{d['total_gb']} GB ({d['percent']}%)")
        procs = self.scan_processes()
        lines.append(f"Processes: {procs['total']} running")
        if procs["top_cpu"]:
            top = procs["top_cpu"][0]
            lines.append(f"Top CPU: {top['name']} ({top['cpu_percent']}%)")
        lines.append(f"Firewall:\n{self.firewall_status()}")
        return "\n".join(lines)

    def handle_natural(self, text: str) -> dict:
        text_lower = text.lower()

        if "kill" in text_lower or "stop process" in text_lower:
            for word in ["kill", "stop"]:
                idx = text_lower.find(word)
                if idx >= 0:
                    rest = text[idx + len(word) :].strip().removeprefix("process ").strip()
                    if rest:
                        return {"success": True, "result": self.kill_process(rest)}
            return {"success": False, "error": "Specify process name to kill"}

        if "firewall" in text_lower:
            # Word-boundary match: "connection" contains "on" as a substring,
            # so a status query must not toggle the firewall.
            if re.search(r"\bon\b", text_lower) or "enable" in text_lower:
                return {"success": True, "result": f"🛡️ Firewall enabled:\n{self.firewall_toggle(True)}"}
            if re.search(r"\boff\b", text_lower) or "disable" in text_lower:
                return {"success": True, "result": f"⚠️ Firewall disabled:\n{self.firewall_toggle(False)}"}
            return {"success": True, "result": f"🛡️ Firewall Status:\n{self.firewall_status()}"}

        if "service" in text_lower:
            action = "status"
            # "restart" contains "start" — check longest first.
            for a in ["restart", "stop", "start"]:
                if re.search(r"\b" + a + r"\b", text_lower):
                    action = a
                    break
            name_match = re.search(r"(?:service\s+)?(\w+)\s*", text, re.IGNORECASE)
            if name_match:
                svc = name_match.group(1)
                if svc.lower() in ("start", "stop", "restart", "service"):
                    svc = text.split()[-1]
                return {"success": True, "result": self.service_control(svc, action)}
            return {"success": False, "error": "Specify service name"}

        if "defender" in text_lower or "virus" in text_lower or "scan" in text_lower:
            return {"success": True, "result": f"🔬 Defender Quick Scan:\n{self.defender_scan()}"}

        if "connection" in text_lower or "active" in text_lower or "established" in text_lower:
            return {"success": True, "result": f"🌐 Active Connections:\n{self.active_connections()}"}

        if "disk" in text_lower or "storage" in text_lower:
            disks = self.disk_usage()
            return {
                "success": True,
                "result": "\n".join(
                    [f"{d['device']}: {d['used_gb']}/{d['total_gb']} GB ({d['percent']}%)" for d in disks]
                ),
            }

        if "port" in text_lower:
            ports = self.scan_ports()
            return {"success": True, "result": "Open ports:\n" + "\n".join(ports[:10])}

        if "process" in text_lower:
            procs = self.scan_processes()
            return {
                "success": True,
                "result": "Top processes:\n"
                + "\n".join([f"{p['name']}: {p['cpu_percent']}% CPU" for p in procs["top_cpu"][:5]]),
            }

        if "network" in text_lower:
            return {"success": True, "result": f"🌐 Network Config:\n{self.scan_network()}"}

        return {"success": True, "result": self.full_report()}
