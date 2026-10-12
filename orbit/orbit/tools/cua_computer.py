# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT CuaDriver Wrapper — vision-based computer use agent.

Replaces the old ComputerTool with background-safe screen understanding:
  - Take screenshots (background, no focus steal)
  - Get UI accessibility tree (clickable element indices)
  - Click, type, scroll by element or by coordinate
  - Launch apps without stealing focus
  - Full desktop state snapshot

Requires: pip install cua-driver + cua-driver serve running
"""

import base64
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any
from ..logging_config import get_logger

logger = get_logger(__name__)

_BINARY_PATH: Optional[str] = None


def _get_binary() -> Optional[str]:
    global _BINARY_PATH
    if _BINARY_PATH:
        return _BINARY_PATH
    try:
        from cua_driver import get_binary_path

        _BINARY_PATH = str(get_binary_path())
        return _BINARY_PATH
    except Exception as e:
        logger.warning("cua-driver not installed: %s", e)
        return None


def _call_tool(tool: str, args: dict, timeout: int = 30) -> Dict[str, Any]:
    bp = _get_binary()
    if not bp:
        return {"error": "cua-driver not installed. `pip install cua-driver`"}
    try:
        r = subprocess.run(
            [bp, tool],
            input=json.dumps(args).encode(),
            capture_output=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", "replace")[:500]
            return {"error": f"cua-driver {tool} failed: {err}", "returncode": r.returncode}
        output = r.stdout.decode("utf-8", "replace")
        if not output.strip():
            return {"status": "ok"}
        return json.loads(output)
    except subprocess.TimeoutExpired:
        return {"error": f"cua-driver {tool} timed out after {timeout}s"}
    except json.JSONDecodeError as e:
        return {
            "error": f"cua-driver {tool} returned invalid JSON: {e}",
            "raw": r.stdout.decode("utf-8", "replace")[:500],
        }
    except Exception as e:
        return {"error": f"cua-driver {tool} exception: {e}"}


def is_available() -> bool:
    bp = _get_binary()
    if not bp:
        return False
    r = _call_tool("health_report", {})
    return "error" not in r


def ensure_daemon() -> Dict[str, Any]:
    bp = _get_binary()
    if not bp:
        return {"error": "cua-driver not installed"}
    r = _call_tool("health_report", {})
    if "error" not in r:
        return {"status": "running"}
    try:
        subprocess.Popen(
            [bp, "serve"],
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return {"status": "started"}
    except Exception as e:
        return {"error": str(e)}


class CuaComputerTool:
    def __init__(self):
        self.name = "cua_computer"
        self.description = "Vision-based computer automation (CuaDriver)"

    def get_screen_size(self) -> Dict[str, Any]:
        return _call_tool("get_screen_size", {})

    def list_apps(self) -> Dict[str, Any]:
        return _call_tool("list_apps", {})

    def list_windows(self, pid: int) -> Dict[str, Any]:
        return _call_tool("list_windows", {"pid": pid})

    def launch_app(self, name: str = "", path: str = "", aumid: str = "") -> Dict[str, Any]:
        args: Dict[str, Any] = {}
        if name:
            args["name"] = name
        if path:
            args["path"] = path
        if aumid:
            args["aumid"] = aumid
        if not args:
            return {"error": "Provide name, path, or aumid"}
        return _call_tool("launch_app", args)

    def get_window_state(
        self, pid: int, window_id: int, max_elements: int = 500, max_depth: int = 10
    ) -> Dict[str, Any]:
        return _call_tool(
            "get_window_state",
            {
                "pid": pid,
                "window_id": window_id,
                "max_elements": max_elements,
                "max_depth": max_depth,
            },
            timeout=60,
        )

    def get_desktop_state(self, session_id: str = "orbit") -> Dict[str, Any]:
        return _call_tool("get_desktop_state", {"session": session_id}, timeout=60)

    def screenshot(self) -> Dict[str, Any]:
        bp = _get_binary()
        if not bp:
            return {"error": "cua-driver not installed"}
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        try:
            r = subprocess.run(
                [bp, "screenshot", "--out-file", tmp],
                input=b"{}",
                capture_output=True,
                timeout=30,
            )
            if r.returncode != 0 or not Path(tmp).stat().st_size:
                Path(tmp).unlink(missing_ok=True)
                return {"error": "screenshot failed"}
            with open(tmp, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            Path(tmp).unlink(missing_ok=True)
            return {"screenshot_base64": b64, "format": "png"}
        except Exception as e:
            Path(tmp).unlink(missing_ok=True)
            return {"error": str(e)}

    def click(
        self,
        pid: int,
        window_id: Optional[int] = None,
        element_index: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {"pid": pid}
        if window_id:
            args["window_id"] = window_id
        if element_index is not None:
            args["element_index"] = element_index
        if x is not None and y is not None:
            args["x"] = x
            args["y"] = y
        return _call_tool("click", args)

    def right_click(self, pid: int, window_id: int, element_index: int) -> Dict[str, Any]:
        return _call_tool(
            "right_click",
            {"pid": pid, "window_id": window_id, "element_index": element_index},
        )

    def double_click(self, pid: int, window_id: int, element_index: int) -> Dict[str, Any]:
        return _call_tool(
            "double_click",
            {"pid": pid, "window_id": window_id, "element_index": element_index},
        )

    def type_text(
        self,
        pid: int,
        text: str,
        window_id: Optional[int] = None,
        element_index: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {"pid": pid, "text": text}
        if window_id:
            args["window_id"] = window_id
        if element_index is not None:
            args["element_index"] = element_index
        if x is not None and y is not None:
            args["x"] = x
            args["y"] = y
        return _call_tool("type_text", args)

    def scroll(
        self,
        pid: int,
        direction: str = "down",
        amount: int = 3,
        window_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {
            "pid": pid,
            "direction": direction,
            "amount": amount,
        }
        if window_id:
            args["window_id"] = window_id
        return _call_tool("scroll", args)

    def hotkey(self, pid: int, keys: List[str]) -> Dict[str, Any]:
        return _call_tool("hotkey", {"pid": pid, "keys": keys})

    def press_key(
        self,
        pid: int,
        key: str,
        window_id: Optional[int] = None,
        element_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {"pid": pid, "key": key}
        if window_id:
            args["window_id"] = window_id
        if element_index is not None:
            args["element_index"] = element_index
        return _call_tool("press_key", args)

    def get_cursor_position(self) -> Dict[str, Any]:
        return _call_tool("get_cursor_position", {})

    def move_cursor(self, x: int, y: int) -> Dict[str, Any]:
        return _call_tool("move_cursor", {"x": x, "y": y})

    def bring_to_front(self, pid: int, window_id: Optional[int] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {"pid": pid}
        if window_id:
            args["window_id"] = window_id
        return _call_tool("bring_to_front", args)

    def kill_app(self, pid: int) -> Dict[str, Any]:
        return _call_tool("kill_app", {"pid": pid})

    def start_session(self, session_id: str) -> Dict[str, Any]:
        return _call_tool(
            "start_session",
            {"session": session_id, "capture_scope": "auto"},
        )

    def end_session(self, session_id: str) -> Dict[str, Any]:
        return _call_tool("end_session", {"session": session_id})

    def health_report(self) -> Dict[str, Any]:
        return _call_tool("health_report", {})

    def debug_window_info(self, pid: int) -> Dict[str, Any]:
        return _call_tool("debug_window_info", {"pid": pid})
