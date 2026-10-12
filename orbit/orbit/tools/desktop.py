# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
DesktopAutomationTool — Full mouse, keyboard, window, and app control via pyautogui.

Capabilities:
- Mouse: move, click, double-click, right-click, drag, scroll
- Keyboard: type text, press keys, hotkey combos
- Window: list windows, focus, minimize, maximize, close, resize, move
- App: launch any app by name or path, list running apps
- Screen: get screen size, take screenshot, get pixel color
- Clipboard: get/set clipboard content
"""

import os
import platform
import subprocess
import re
from pathlib import Path
from typing import Optional

from ..logging_config import get_logger
from ..core.policy_engine import check_tool

logger = get_logger(__name__)

try:
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False

try:
    import pyperclip

    PYPERCLIP_AVAILABLE = True
except ImportError:
    PYPERCLIP_AVAILABLE = False


def _check():
    if not PYAUTOGUI_AVAILABLE:
        raise RuntimeError("pyautogui not installed. pip install pyautogui")


class DesktopAutomationTool:
    def __init__(self):
        self.os = platform.system()

    def _gate(self, action: str) -> Optional[dict]:
        decision = check_tool("desktop", action)
        if not decision.allowed:
            return {"error": f"Access denied ({decision.reason}): desktop.{action}", "policy": decision.to_dict()}
        return None

    def is_available(self) -> bool:
        return PYAUTOGUI_AVAILABLE

    def _ps(self, cmd: str) -> dict:
        g = self._gate("ps")
        if g:
            return g
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return {"success": r.returncode == 0, "stdout": r.stdout.strip()[:3000], "stderr": r.stderr.strip()[:500]}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _ps_quote(value: str) -> str:
        """Escape a value for safe inclusion in a PowerShell single-quoted string."""
        return str(value).replace("'", "''")

    @staticmethod
    def _validate_pid(pid) -> int:
        """Validate PID is a safe positive integer. Raises ValueError if not."""
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid PID: {pid!r} — must be an integer")
        if pid <= 0 or pid > 4194304:  # Windows max PID
            raise ValueError(f"Invalid PID: {pid} — must be 1-4194304")
        return pid

    @staticmethod
    def _validate_process_name(name: str) -> str:
        """Validate process name contains only safe characters (alphanumeric, space, dot, dash, underscore)."""
        import re

        name = str(name).strip()
        if not name:
            raise ValueError("Empty process name")
        if not re.match(r"^[a-zA-Z0-9 ._-]+$", name):
            raise ValueError(f"Invalid process name: {name!r} — only alphanumeric, spaces, dots, dashes allowed")
        return name

    # ── Mouse ──────────────────────────────────────────────────────────────

    def mouse_move(self, x: int, y: int, duration: float = 0.3) -> dict:
        _check()
        try:
            pyautogui.moveTo(x, y, duration=duration)
            return {"success": True, "x": x, "y": y}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mouse_click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left") -> dict:
        _check()
        g = self._gate("mouse_click")
        if g:
            return g
        try:
            if x is not None and y is not None:
                pyautogui.click(x, y, button=button)
            else:
                pyautogui.click(button=button)
            pos = pyautogui.position()
            return {"success": True, "x": pos.x, "y": pos.y, "button": button}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mouse_double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> dict:
        _check()
        g = self._gate("mouse_double_click")
        if g:
            return g
        try:
            if x is not None and y is not None:
                pyautogui.doubleClick(x, y)
            else:
                pyautogui.doubleClick()
            pos = pyautogui.position()
            return {"success": True, "x": pos.x, "y": pos.y}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mouse_right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> dict:
        _check()
        g = self._gate("mouse_right_click")
        if g:
            return g
        try:
            if x is not None and y is not None:
                pyautogui.rightClick(x, y)
            else:
                pyautogui.rightClick()
            pos = pyautogui.position()
            return {"success": True, "x": pos.x, "y": pos.y}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mouse_drag(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5, button: str = "left"
    ) -> dict:
        _check()
        g = self._gate("mouse_drag")
        if g:
            return g
        try:
            pyautogui.moveTo(start_x, start_y)
            pyautogui.drag(end_x - start_x, end_y - start_y, duration=duration, button=button)
            return {"success": True, "from": [start_x, start_y], "to": [end_x, end_y]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mouse_scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> dict:
        _check()
        g = self._gate("mouse_scroll")
        if g:
            return g
        try:
            if x is not None and y is not None:
                pyautogui.scroll(clicks, x, y)
            else:
                pyautogui.scroll(clicks)
            return {"success": True, "clicks": clicks, "direction": "up" if clicks > 0 else "down"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mouse_position(self) -> dict:
        _check()
        try:
            pos = pyautogui.position()
            return {"success": True, "x": pos.x, "y": pos.y}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Keyboard ───────────────────────────────────────────────────────────

    def type_text(self, text: str, interval: float = 0.02) -> dict:
        _check()
        g = self._gate("type_text")
        if g:
            return g
        try:
            if text.isascii():
                pyautogui.typewrite(text, interval=interval)
            else:
                return self.type_unicode(text)
            return {"success": True, "text": text[:100]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def type_unicode(self, text: str) -> dict:
        _check()
        g = self._gate("type_unicode")
        if g:
            return g
        try:
            if PYPERCLIP_AVAILABLE:
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            else:
                for char in text:
                    pyautogui.press(char) if char.isascii() else None
            return {"success": True, "text": text[:100]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def press_key(self, key: str) -> dict:
        _check()
        g = self._gate("press_key")
        if g:
            return g
        try:
            pyautogui.press(key)
            return {"success": True, "key": key}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def hotkey(self, *keys: str) -> dict:
        _check()
        g = self._gate("hotkey")
        if g:
            return g
        try:
            pyautogui.hotkey(*keys)
            return {"success": True, "keys": list(keys)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def key_down(self, key: str) -> dict:
        _check()
        g = self._gate("key_down")
        if g:
            return g
        try:
            pyautogui.keyDown(key)
            return {"success": True, "key": key, "state": "down"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def key_up(self, key: str) -> dict:
        _check()
        g = self._gate("key_up")
        if g:
            return g
        try:
            pyautogui.keyUp(key)
            return {"success": True, "key": key, "state": "up"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Clipboard ──────────────────────────────────────────────────────────

    def get_clipboard(self) -> dict:
        if PYPERCLIP_AVAILABLE:
            try:
                text = pyperclip.paste()
                return {"success": True, "text": text[:2000]}
            except Exception as e:
                return {"success": False, "error": str(e)}
        return self._ps("Get-Clipboard")

    def set_clipboard(self, text: str) -> dict:
        g = self._gate("clipboard_write")
        if g:
            return g
        if PYPERCLIP_AVAILABLE:
            try:
                pyperclip.copy(text)
                return {"success": True, "text": text[:100]}
            except Exception as e:
                return {"success": False, "error": str(e)}
        return self._ps(f"Set-Clipboard -Value '{self._ps_quote(text)}'")

    # ── Screen ─────────────────────────────────────────────────────────────

    def screen_size(self) -> dict:
        _check()
        try:
            size = pyautogui.size()
            return {"success": True, "width": size.width, "height": size.height}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def screenshot(self, region: Optional[list] = None) -> dict:
        _check()
        try:
            import io
            import base64

            if region:
                img = pyautogui.screenshot(region=tuple(region))
            else:
                img = pyautogui.screenshot()
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return {"success": True, "base64": b64[:50000], "width": img.width, "height": img.height}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def pixel_color(self, x: int, y: int) -> dict:
        _check()
        try:
            img = pyautogui.screenshot(region=(x, y, 1, 1))
            rgb = img.getpixel((0, 0))
            return {
                "success": True,
                "x": x,
                "y": y,
                "r": rgb[0],
                "g": rgb[1],
                "b": rgb[2],
                "hex": f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Window Management (Windows) ────────────────────────────────────────

    def list_windows(self) -> dict:
        if self.os != "Windows":
            return {"success": False, "error": "Window management only on Windows"}
        ps = """
        Get-Process | Where-Object {$_.MainWindowTitle -ne ''} |
        Select-Object Id, ProcessName, MainWindowTitle |
        ConvertTo-Json -Compress
        """
        r = self._ps(ps)
        if r.get("success") and r.get("stdout"):
            try:
                import json

                data = json.loads(r["stdout"])
                if isinstance(data, dict):
                    data = [data]
                windows = [
                    {"pid": w.get("Id"), "name": w.get("ProcessName"), "title": w.get("MainWindowTitle")} for w in data
                ]
                return {"success": True, "windows": windows, "count": len(windows)}
            except Exception:
                logger.exception("Desktop list_windows JSON parse failed")
        return {"success": True, "windows": [], "count": 0}

    def focus_window(self, pid: int) -> dict:
        if self.os != "Windows":
            return {"success": False, "error": "Window management only on Windows"}
        try:
            pid = self._validate_pid(pid)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        g = self._gate("focus_window")
        if g:
            return g
        ps = f"""
        Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class WinAPI {{
            [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
            [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        }}
"@
        $proc = Get-Process -Id {pid} -ErrorAction SilentlyContinue
        if ($proc) {{
            [WinAPI]::ShowWindow($proc.MainWindowHandle, 9) | Out-Null
            [WinAPI]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
            "focused"
        }} else {{
            "process_not_found"
        }}
        """
        r = self._ps(ps)
        if r.get("success") and "focused" in r.get("stdout", ""):
            return {"success": True, "pid": pid, "action": "focused"}
        return {"success": False, "error": f"Could not focus PID {pid}"}

    def minimize_window(self, pid: int) -> dict:
        if self.os != "Windows":
            return {"success": False, "error": "Window management only on Windows"}
        try:
            pid = self._validate_pid(pid)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        g = self._gate("minimize_window")
        if g:
            return g
        ps = f"""
        Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class WinAPI {{
            [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        }}
"@
        $proc = Get-Process -Id {pid} -ErrorAction SilentlyContinue
        if ($proc) {{
            [WinAPI]::ShowWindow($proc.MainWindowHandle, 6) | Out-Null
            "minimized"
        }} else {{
            "process_not_found"
        }}
        """
        r = self._ps(ps)
        if r.get("success") and "minimized" in r.get("stdout", ""):
            return {"success": True, "pid": pid, "action": "minimized"}
        return {"success": False, "error": f"Could not minimize PID {pid}"}

    def maximize_window(self, pid: int) -> dict:
        if self.os != "Windows":
            return {"success": False, "error": "Window management only on Windows"}
        try:
            pid = self._validate_pid(pid)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        g = self._gate("maximize_window")
        if g:
            return g
        ps = f"""
        Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class WinAPI {{
            [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        }}
"@
        $proc = Get-Process -Id {pid} -ErrorAction SilentlyContinue
        if ($proc) {{
            [WinAPI]::ShowWindow($proc.MainWindowHandle, 3) | Out-Null
            "maximized"
        }} else {{
            "process_not_found"
        }}
        """
        r = self._ps(ps)
        if r.get("success") and "maximized" in r.get("stdout", ""):
            return {"success": True, "pid": pid, "action": "maximized"}
        return {"success": False, "error": f"Could not maximize PID {pid}"}

    def close_window(self, pid: int) -> dict:
        try:
            pid = self._validate_pid(pid)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        g = self._gate("close_window")
        if g:
            return g
        try:
            import psutil

            proc = psutil.Process(pid)
            proc.terminate()
            return {"success": True, "pid": pid, "action": "closed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def move_window(self, pid: int, x: int, y: int, width: int, height: int) -> dict:
        if self.os != "Windows":
            return {"success": False, "error": "Window management only on Windows"}
        try:
            pid = self._validate_pid(pid)
            x, y, width, height = int(x), int(y), int(width), int(height)
        except (ValueError, TypeError) as e:
            return {"success": False, "error": str(e)}
        g = self._gate("move_window")
        if g:
            return g
        ps = f"""
        Add-Type @"
        using System;
        using System.Runtime.InteropServices;
        public class WinAPI {{
            [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
        }}
"@
        $proc = Get-Process -Id {pid} -ErrorAction SilentlyContinue
        if ($proc) {{
            [WinAPI]::MoveWindow($proc.MainWindowHandle, {x}, {y}, {width}, {height}, $true) | Out-Null
            "moved"
        }} else {{
            "process_not_found"
        }}
        """
        r = self._ps(ps)
        if r.get("success") and "moved" in r.get("stdout", ""):
            return {"success": True, "pid": pid, "x": x, "y": y, "width": width, "height": height}
        return {"success": False, "error": f"Could not move PID {pid}"}

    # ── App Launching ──────────────────────────────────────────────────────

    APP_MAP = {
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
        "obs": "obs64",
        "obsidian": "obsidian",
        "blender": "blender",
        "photoshop": "photoshop",
        "figma": "figma",
        "postman": "postman",
        "docker": "docker",
        "git bash": "git-bash",
        "sublime": "sublime_text",
        "notepad++": "notepad++",
        "brave": "brave",
        "opera": "opera",
        "steam": "steam",
        "epic games": "epicgameslauncher",
    }

    def open_app(self, app_name: str, path: Optional[str] = None) -> dict:
        g = self._gate("open_app")
        if g:
            return g
        app_name = app_name.strip().strip("'\"")
        if not app_name and not path:
            return {"success": False, "error": "App name or path required"}

        if path:
            # A raw path flows straight to os.startfile/Start-Process
            # (arbitrary binary execution). Gate the resolved path and
            # audit it; refuse script/unsafe extensions outright.
            from ..core.policy_engine import get_policy_engine

            try:
                real = str(Path(path).expanduser().resolve())
            except Exception:
                return {"success": False, "error": f"Invalid path: {path}"}
            decision = get_policy_engine().check_path(real, mutate=False)
            if not decision.allowed:
                try:
                    get_policy_engine()._audit("desktop.open_app", real, False, decision.reason)
                except Exception:
                    pass
                return {"success": False, "error": f"Path not allowed ({decision.reason}): {path}"}
            if real.lower().endswith((".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".lnk", ".scr", ".msi")):
                return {"success": False, "error": f"Refusing to execute script/installer path: {path}"}
            path = real

        if self.os == "Windows":
            exe = path or self.APP_MAP.get(app_name.lower(), app_name)
            try:
                os.startfile(exe)
                return {"success": True, "app": app_name, "exe": exe}
            except FileNotFoundError:
                r = self._ps(f"Start-Process '{self._ps_quote(exe)}' -ErrorAction Stop")
                if r.get("success"):
                    return {"success": True, "app": app_name, "exe": exe}
                return {"success": False, "error": f"Could not find '{app_name}'. App installed hai?"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        return {"success": False, "error": f"App launching not supported on {self.os}"}

    def open_url(self, url: str) -> dict:
        import webbrowser

        g = self._gate("open_url")
        if g:
            return g
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            webbrowser.open(url)
            return {"success": True, "url": url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_running_apps(self) -> dict:
        if self.os == "Windows":
            r = self._ps(
                "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json"
            )
            if r.get("success") and r.get("stdout"):
                try:
                    import json

                    data = json.loads(r["stdout"])
                    if isinstance(data, dict):
                        data = [data]
                    apps = [
                        {"pid": a.get("Id"), "name": a.get("ProcessName"), "title": a.get("MainWindowTitle")}
                        for a in data
                    ]
                    return {"success": True, "apps": apps, "count": len(apps)}
                except Exception:
                    logger.exception("Desktop list_running_apps JSON parse failed")
        return {"success": True, "apps": [], "count": 0}

    def launch_explorer(self, path: str = "C:\\") -> dict:
        return self.open_app("explorer", path=path)

    # ── Natural Language Handler ───────────────────────────────────────────

    def handle_natural(self, text: str) -> dict:
        t = text.lower().strip()

        # Mouse move
        m = re.match(r"(?:move|shift)\s+(?:mouse|cursor)\s+to\s+(\d+)\s*[,x]\s*(\d+)", t)
        if m:
            return self.mouse_move(int(m.group(1)), int(m.group(2)))

        # Click
        m = re.match(r"(?:click|tap)\s+(?:at\s+)?(\d+)\s*[,x]\s*(\d+)", t)
        if m:
            return self.mouse_click(int(m.group(1)), int(m.group(2)))

        if re.match(r"(?:click|tap)", t):
            return self.mouse_click()

        # Double click
        if "double click" in t or "double tap" in t:
            m = re.search(r"(\d+)\s*[,x]\s*(\d+)", t)
            if m:
                return self.mouse_double_click(int(m.group(1)), int(m.group(2)))
            return self.mouse_double_click()

        # Right click
        if "right click" in t or "right tap" in t:
            m = re.search(r"(\d+)\s*[,x]\s*(\d+)", t)
            if m:
                return self.mouse_right_click(int(m.group(1)), int(m.group(2)))
            return self.mouse_right_click()

        # Scroll
        m = re.match(r"(?:scroll|wheel)\s+(up|down)\s*(?:by\s+)?(\d+)?", t)
        if m:
            clicks = int(m.group(2)) if m.group(2) else 3
            return self.mouse_scroll(clicks if m.group(1) == "up" else -clicks)

        # File operations — BEFORE type/clipboard branches, which would
        # otherwise swallow them ("write file x" -> type_unicode,
        # "copy file a to b" -> set_clipboard).
        m = re.match(
            r"(?:write|create)\s+file\s+(\S+)\s+(?:with\s+)?(.*)", text, re.IGNORECASE | re.DOTALL
        )
        if m:
            from .file_tools import FileTools

            r = FileTools().write(m.group(1).strip(), m.group(2).strip())
            return r
        m = re.match(r"(?:copy)\s+file\s+(\S+)\s+to\s+(\S+)", text, re.IGNORECASE)
        if m:
            from .file_tools import FileTools

            return FileTools().copy(m.group(1).strip(), m.group(2).strip())
        m = re.match(r"(?:move|rename)\s+file\s+(\S+)\s+to\s+(\S+)", text, re.IGNORECASE)
        if m:
            from .file_tools import FileTools

            return FileTools().move(m.group(1).strip(), m.group(2).strip())
        m = re.match(r"(?:delete|remove)\s+file\s+(\S+)", text, re.IGNORECASE)
        if m:
            from .file_tools import FileTools

            return FileTools().delete(m.group(1).strip())

        # Type text
        m = re.match(r"(?:type|write|enter)\s+(.+)", t)
        if m:
            return self.type_unicode(m.group(1).strip())

        # Press key
        m = re.match(r"(?:press|hit|tap)\s+(?:key\s+)?(.+)", t)
        if m:
            key = m.group(1).strip().lower()
            return self.press_key(key)

        # Hotkey
        m = re.match(r"(?:hotkey|shortcut|combo)\s+(.+)", t)
        if m:
            keys = [k.strip() for k in m.group(1).split("+")]
            return self.hotkey(*keys)

        # Ctrl+C, Ctrl+V etc.
        m = re.match(r"(ctrl\+\w|alt\+\w|shift\+\w|ctrl\+shift\+\w)", t)
        if m:
            keys = [k.strip() for k in m.group(1).split("+")]
            return self.hotkey(*keys)

        # Open app
        m = re.match(r"(?:open|launch|start|khol|kholo)\s+(.+)", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
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
            }
            if target.lower() in web_shortcuts:
                return self.open_url(web_shortcuts[target.lower()])
            # URL heuristic: require a known TLD or scheme. The old check
            # (" " not in target and "." in target) treated "file.txt"
            # as a URL and opened a browser for it.
            if re.match(r"^(https?://|www\.)", target, re.IGNORECASE) or re.search(
                r"\.(com|org|net|io|dev|app|in|co|ai|gov|edu|info|biz)([:/]|$)",
                target,
                re.IGNORECASE,
            ):
                return self.open_url(target)
            return self.open_app(target)

        # Close/kill app
        m = re.match(r"(?:close|kill|band|stop)\s+(.+)", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            try:
                target = self._validate_process_name(target)
            except ValueError as e:
                return {"success": False, "error": str(e)}
            r = self._ps(
                f"Get-Process -Name '*{self._ps_quote(target)}*' | Stop-Process -Force -ErrorAction SilentlyContinue"
            )
            return {"success": True, "action": f"Closed {target}"} if r.get("success") else r

        # List windows
        if any(w in t for w in ["list windows", "which windows", "open windows", "running apps", "windows list"]):
            return self.list_windows()

        # Focus window
        m = re.match(r"(?:focus|switch to|bring)\s+(?:window\s+)?(.+)", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            r = self._ps(f"(Get-Process -Name '*{self._ps_quote(target)}*' -ErrorAction SilentlyContinue).Id")
            if r.get("success") and r.get("stdout", "").strip().isdigit():
                return self.focus_window(int(r["stdout"].strip()))
            return {"success": False, "error": f"No window found for '{target}'"}

        # Minimize
        m = re.match(r"(?:minimize|minimise|min)\s+(.+)", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            r = self._ps(f"(Get-Process -Name '*{self._ps_quote(target)}*' -ErrorAction SilentlyContinue).Id")
            if r.get("success") and r.get("stdout", "").strip().isdigit():
                return self.minimize_window(int(r["stdout"].strip()))
            return {"success": False, "error": f"No window found for '{target}'"}

        # Maximize
        m = re.match(r"(?:maximize|maximise|max)\s+(.+)", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            r = self._ps(f"(Get-Process -Name '*{self._ps_quote(target)}*' -ErrorAction SilentlyContinue).Id")
            if r.get("success") and r.get("stdout", "").strip().isdigit():
                return self.maximize_window(int(r["stdout"].strip()))
            return {"success": False, "error": f"No window found for '{target}'"}

        # Screen size
        if "screen size" in t or "resolution" in t:
            return self.screen_size()

        # Mouse position
        if "mouse position" in t or "cursor position" in t or "where is mouse" in t:
            return self.mouse_position()

        # Screenshot
        if "screenshot" in t or "screen capture" in t:
            return self.screenshot()

        # Clipboard — requires explicit clipboard intent. The old branch
        # fired on any "copy"/"paste" ("copy file a to b" became a
        # clipboard write instead of a file copy).
        if "clipboard" in t or re.search(r"\bcopy\b.{0,30}\bclipboard\b|\bto\s+clipboard\b", t):
            if any(w in t for w in ["copy", "set", "write"]):
                m = re.search(r"(?:copy|set|write)\s+(?:to\s+)?(?:clipboard\s+)?(.+)", text, re.IGNORECASE)
                if m:
                    return self.set_clipboard(m.group(1).strip())
            return self.get_clipboard()
        if re.match(r"paste\b", t):
            return self.get_clipboard()

        return {
            "success": False,
            "error": "Command samajh nahi aaya. Try: click 100,200 / type hello / open chrome / scroll up / press enter",
        }


def get_desktop_tool() -> DesktopAutomationTool:
    return DesktopAutomationTool()
