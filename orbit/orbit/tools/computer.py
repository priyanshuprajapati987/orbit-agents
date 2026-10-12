# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import os
import platform
import re
import shlex
import subprocess
from typing import Optional
from ..core.policy_engine import check_tool

ALLOWED_COMMANDS = {
    "tasklist",
    "ipconfig",
    "systeminfo",
    "netstat",
    "ping",
    "tracert",
    "pathping",
    "driverquery",
    "powercfg",
    "chkdsk",
    "sfc",
    "wmic",
    "ps",
    "df",
    "free",
    "uname",
    "uptime",
}


class ComputerTool:
    def __init__(self):
        self.os = platform.system()

    def _gate(self, action: str) -> Optional[dict]:
        decision = check_tool("system", action)
        if not decision.allowed:
            return {
                "success": False,
                "error": f"Access denied ({decision.reason}): {action}",
                "policy": decision.to_dict(),
            }
        return None

    @staticmethod
    def _ps_quote(s):
        s = str(s).replace("'", "''")
        s = s.replace("\n", "`n")
        s = s.replace("\r", "`r")
        s = s.replace("\t", "`t")
        return "'" + s + "'"

    def _run_powershell(self, cmd: str) -> dict:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout.strip()[:2000],
                "stderr": result.stderr.strip()[:500],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Command timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_clipboard(self):
        result = self._run_powershell("Get-Clipboard")
        if result.get("success") and not result.get("stdout", "").strip():
            result["stdout"] = (
                "Clipboard mein image hai jo direct text mein convert nahi ho sakta. Pehle image se text manually copy karo."
            )
        return result

    def set_clipboard(self, text: str):
        g = self._gate("clipboard_write")
        if g:
            return g
        return self._run_powershell(f"Set-Clipboard -Value {self._ps_quote(text)}")

    def run_command(self, command: str) -> dict:
        cmd = command.strip().lower()
        # Security: block shell metacharacters before allowlist check
        DANGEROUS_CHARS = set(";&|`$(){}[]!#~")
        if any(c in command for c in DANGEROUS_CHARS):
            return {"success": False, "error": "Shell metacharacters not allowed"}
        base = cmd.split()[0] if cmd else ""
        if base not in ALLOWED_COMMANDS:
            return {"success": False, "error": f"Command '{base}' not allowed. ORBIT sirf safe commands run karta hai."}
        try:
            args = shlex.split(command, posix=False) if platform.system() == "Windows" else shlex.split(command)
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout.strip()[:2000],
                "stderr": result.stderr.strip()[:500],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Command timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_processes(self):
        if self.os == "Windows":
            return self._run_powershell("Get-Process | Select-Object Name,Id,CPU,WorkingSet | ConvertTo-Json")
        return self.run_command("ps aux --no-headers")

    def list_files(self, path: str = "."):
        if self.os == "Windows":
            # Compact one-name-per-line format with an exact count header —
            # the old ConvertTo-Json output was truncated at 2000 chars by
            # _run_powershell, breaking both parsing and counts.
            cmd = (
                f"$items = @(Get-ChildItem -Path {self._ps_quote(path)}); "
                "$py = @($items | Where-Object { $_.Name -like '*.py' }).Count; "
                "Write-Output ('__META__ ' + $items.Count + ' ' + $py); "
                "$items | ForEach-Object { $_.Name }"
            )
            return self._run_powershell(cmd)
        return {"success": False, "error": "File listing on non-Windows not implemented"}

    def _list_files_response(self, text_lower: str, path: str) -> dict:
        """List files with counts; filter by type when the ask names one.

        "List all Python files ... and count how many there are" must answer
        with a counted, typed summary — raw JSON never answers a count
        question (E10 file_operations scored 0.0 on it).
        """
        res = self.list_files(path)
        if not res.get("success"):
            return res
        lines = [ln.strip() for ln in str(res.get("stdout") or "").splitlines() if ln.strip()]
        total = 0
        py_total = 0
        if lines and lines[0].startswith("__META__"):
            parts = lines[0].split()
            try:
                total = int(parts[1])
                py_total = int(parts[2])
            except (IndexError, ValueError):
                pass
            lines = lines[1:]
        names = [ln for ln in lines if not ln.startswith("__META__")]
        type_map = [
            ("python", ".py", "Python"),
            ("javascript", ".js", "JavaScript"),
            ("js", ".js", "JavaScript"),
            ("json", ".json", "JSON"),
            ("csv", ".csv", "CSV"),
            ("markdown", ".md", "Markdown"),
            ("md", ".md", "Markdown"),
            ("text", ".txt", "Text"),
            ("txt", ".txt", "Text"),
            ("html", ".html", "HTML"),
            ("css", ".css", "CSS"),
            ("log", ".log", "Log"),
        ]
        if "files" in text_lower:
            for kw, suffix, label in type_map:
                if re.search(rf"\b{re.escape(kw)}\b", text_lower):
                    matched = [n for n in names if n.lower().endswith(suffix)]
                    count = py_total if kw in ("python", "py") else len(matched)
                    listing = "\n".join(matched[:50])
                    return {
                        "success": True,
                        "stdout": f"Found {count} {label} files in {path}:\n{listing}",
                    }
        listing = "\n".join(names[:50])
        return {
            "success": True,
            "stdout": f"Found {total} files in {path} ({py_total} Python files):\n{listing}",
        }

    def system_info(self):
        info = {"os": self.os, "hostname": platform.node(), "processor": platform.processor()}
        if self.os == "Windows":
            result = self._run_powershell(
                "Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,FreePhysicalMemory,TotalVisibleMemorySize | ConvertTo-Json"
            )
            if result["success"]:
                info["os_detail"] = result["stdout"]
        return info

    def open_app(self, app_name: str) -> dict:
        g = self._gate("open_app")
        if g:
            return g
        app_name = app_name.strip().strip("'\"")
        if not app_name:
            return {"success": False, "error": "App name required"}

        if self.os == "Windows":
            known = {
                "notepad": "notepad.exe",
                "calc": "calc.exe",
                "calculator": "calc.exe",
                "paint": "mspaint.exe",
                "explorer": "explorer.exe",
                "file explorer": "explorer.exe",
                "cmd": "cmd.exe",
                "command prompt": "cmd.exe",
                "terminal": "wt.exe",
                "powershell": "pwsh.exe",
                "chrome": "chrome",
                "google chrome": "chrome",
                "edge": "msedge",
                "firefox": "firefox",
                "vscode": "code",
                "visual studio code": "code",
                "word": "winword",
                "excel": "excel",
                "powerpoint": "powerpnt",
                "spotify": "spotify",
                "discord": "discord",
                "teams": "ms-teams",
                "slack": "slack",
                "zoom": "Zoom",
            }
            lower = app_name.lower()
            if lower not in known:
                return {
                    "success": False,
                    "error": f"'{app_name}' not in known apps. Available: {', '.join(sorted(known.keys()))}",
                }
            exe = known[lower]

            try:
                os.startfile(exe)
                return {"success": True, "stdout": f"Opened: {app_name}", "stderr": ""}
            except FileNotFoundError:
                r = self._run_powershell(f"Start-Process '{exe}' -ErrorAction Stop")
                if r.get("success"):
                    return {"success": True, "stdout": f"Opened: {app_name}", "stderr": ""}
                return {"success": False, "error": f"Could not find or open '{app_name}'. App installed hai?"}
            except Exception as e:
                return {"success": False, "error": f"Failed to open '{app_name}': {e}"}

        return {"success": False, "error": f"Opening apps not supported on {self.os}"}

    def launch_url(self, url: str) -> dict:
        import webbrowser

        g = self._gate("open_url")
        if g:
            return g
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            webbrowser.open(url)
            return {"success": True, "stdout": f"Opened: {url}", "stderr": ""}
        except Exception as e:
            return {"success": False, "error": f"Failed to open URL: {e}"}

    def handle_natural(self, text: str) -> dict:
        text_lower = text.lower()

        # ── Open app / URL ───────────────────────────────────────────────
        m = re.match(r"(?:open|launch|start|khol|kholo|chalu|run)\s+(.+)", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            # Strip "in <browser>" suffix (e.g. "open google.com in chrome")
            browser_match = re.match(r"(.+?)\s+in\s+(?:the\s+)?(\w+)$", target, re.IGNORECASE)
            if browser_match:
                target = browser_match.group(1).strip()
            # If it looks like a URL or website name
            is_url = (
                target.startswith(("http://", "https://"))
                or any(w in target.lower() for w in [".com", ".org", ".net", ".io", ".edu", ".gov"])
                or re.match(r"^[\w.-]+:\d+", target) is not None  # host:port
            )
            if is_url:
                return self.launch_url(target)
            # Check shortcuts for web apps
            web_shortcuts = {
                "whatsapp": "https://web.whatsapp.com",
                "gmail": "https://mail.google.com",
                "google": "https://google.com",
                "youtube": "https://youtube.com",
                "chatgpt": "https://chat.openai.com",
                "github": "https://github.com",
                "twitter": "https://twitter.com",
                "x": "https://x.com",
                "instagram": "https://instagram.com",
                "facebook": "https://facebook.com",
                "linkedin": "https://linkedin.com",
                "netflix": "https://netflix.com",
                "spotify web": "https://open.spotify.com",
            }
            t_lower = target.lower().strip()
            if t_lower in web_shortcuts:
                return self.launch_url(web_shortcuts[t_lower])
            # Try opening as desktop app
            return self.open_app(target)
        if "clipboard" in text_lower:
            if "copy" in text_lower or "set" in text_lower or "write" in text_lower:
                match = re.search(r"(?:copy|set|write)\s+(?:to\s+)?(?:clipboard\s+)?(.+)", text, re.IGNORECASE)
                if match:
                    val = match.group(1).strip()
                    for w in ["copy", "set", "write", "to", "clipboard"]:
                        val = re.sub(rf"^{w}\s+", "", val, flags=re.IGNORECASE)
                    # "Copy the text 'X' to the clipboard" → strip wrappers
                    val = re.sub(r"^(?:the\s+|a\s+|some\s+)?(?:text|message)\s+", "", val, flags=re.IGNORECASE).strip()
                    val = re.sub(r"\s+to\s+(?:the\s+)?clipboard$", "", val, flags=re.IGNORECASE).strip()
                    if not val:
                        val = match.group(1).strip()
                    return self.set_clipboard(val)
            return self.get_clipboard()
        if "process" in text_lower or "task" in text_lower:
            if "kill" in text_lower or "stop" in text_lower:
                match = re.search(r"(?:kill|stop)\s+(?:process\s+)?(\S+)", text, re.IGNORECASE)
                if match:
                    g = self._gate("process_kill")
                    if g:
                        return g
                    name = match.group(1).replace(".exe", "")
                    return self._run_powershell(f"Stop-Process -Name {self._ps_quote(name)} -Force")
            return self.list_processes()
        if re.search(
            r"\b(?:list|show)\s+(?:the\s+)?(?:all\s+)?(?:\w+\s+){0,3}?(?:files?|dir|directory|folder)\b",
            text_lower,
        ) or text_lower.strip() in (
            "ls",
            "dir",
        ):
            # Prefer an explicit "in/from <path>" locator; else strip the
            # leading list-verb phrase; then drop articles/dir-suffix words.
            # Stop the path phrase at connectors ("in X and count ...").
            m_in = re.search(
                r"\b(?:in|from|inside)\s+(?:the\s+)?(.+?)(?=\s+(?:and|that|with|please|then)\b|$)",
                text,
                re.IGNORECASE,
            )
            if m_in:
                path = m_in.group(1)
            else:
                m2 = re.search(
                    r"\b(?:list|show)\s+(?:the\s+)?(?:all\s+)?(?:\w+\s+){0,3}?(?:files?|dir(?:ectory|s)?|folder)\s*(?:of\s+)?(.*)$",
                    text,
                    re.IGNORECASE,
                )
                path = m2.group(1) if m2 and m2.group(1).strip() else "."
            path = re.sub(r"^(?:the\s+)+", "", path).strip()
            path = re.sub(r"\s+(?:directory|dir|folder)$", "", path).strip()
            path = path.strip().strip("`\"'.,;:")
            if not path or re.fullmatch(r"(?:current|this|working)", path, re.IGNORECASE):
                path = "."
            return self._list_files_response(text_lower, path)
        if "info" in text_lower or "spec" in text_lower or "system" in text_lower:
            return {"success": True, "data": self.system_info()}
        # ── Cua vision operations ────────────────────────────────────────
        if "screenshot" in text_lower or "screen" in text_lower or "ui tree" in text_lower:
            from .cua_computer import CuaComputerTool, is_available, ensure_daemon

            if not is_available():
                ensure_daemon()
            if is_available():
                cc = CuaComputerTool()
                if "ui tree" in text_lower or "uia" in text_lower or "desktop" in text_lower:
                    r = cc.get_desktop_state("orbit")
                    if "error" not in r:
                        return {"success": True, "data": r.get("tree_markdown", "")[:3000]}
                r = cc.screenshot()
                if "error" not in r:
                    return {"success": True, "screenshot_base64": r.get("screenshot_base64", "")}
        return {
            "success": False,
            "error": "ORBIT ye command directly run nahi kar sakta. Koi specific tool batao (clipboard, process, file, etc).",
        }
