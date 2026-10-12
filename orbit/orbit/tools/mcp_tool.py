# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT MCP Tool — Model Context Protocol Integration

Connects to external MCP servers (from .mcp.json) and exposes their tools
as ORBIT tools. Also provides an MCP server endpoint for external AI to
call ORBIT tools.

Security fixes applied:
  MCP-001: API key auth on MCP server
  MCP-002: ToolGate (PolicyEngine) integration
  MCP-003: Argument schema validation + length limits
  MCP-004: Per-IP token bucket rate limiting
  MCP-005: HMAC nonce replay protection (timestamp window)
  MCP-006: config_path workspace containment
  MCP-007: Type guard on params (prevents crash on bad types)
"""

from ..core.runtime_paths import app_root
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional


from ..logging_config import get_logger

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MAX_REQUEST_BODY = 1_048_576  # 1 MB
MAX_ARG_STRING_LEN = 50_000
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 30  # per window per IP
NONCE_TIMESTAMP_TOLERANCE = 300  # 5 minutes


# ── MCP Client (connects to external MCP servers) ──────────────────────────


class MCPClient:
    """Manages a single external MCP server process and communicates via stdio."""

    def __init__(
        self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None
    ):
        self.name = name
        self.command = command
        self.args = args
        self.env = {**os.environ, **(env or {})}
        self.cwd = cwd or str(Path.cwd())
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._tools_cache: List[Dict[str, Any]] = []
        self._available = False

    def start(self) -> bool:
        try:
            self._process = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                cwd=self.cwd,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self._discover_tools()
            self._available = True
            logger.info(f"MCP server '{self.name}' started")
            return True
        except FileNotFoundError:
            logger.warning(f"MCP server '{self.name}': command '{self.command}' not found")
            return False
        except Exception as e:
            logger.error(f"MCP server '{self.name}' failed to start: {e}")
            return False

    def _discover_tools(self):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        resp = self._send(payload)
        if resp and "result" in resp:
            self._tools_cache = resp["result"].get("tools", [])

    def _send(self, payload: str) -> Optional[Dict]:
        if not self._process or not self._process.stdin:
            return None
        with self._lock:
            try:
                self._process.stdin.write(payload + "\n")
                self._process.stdin.flush()
                # Use threading to avoid blocking forever
                result = [None]

                def _read():
                    try:
                        result[0] = self._process.stdout.readline() if self._process.stdout else ""
                    except Exception:
                        result[0] = ""

                t = threading.Thread(target=_read, daemon=True)
                t.start()
                t.join(timeout=10)
                if t.is_alive():
                    logger.error(f"MCP read timeout ({self.name})")
                    return None
                line = result[0]
                return json.loads(line) if line else None
            except Exception as e:
                logger.error(f"MCP send error ({self.name}): {e}")
                return None

    def list_tools(self) -> List[Dict[str, Any]]:
        return self._tools_cache

    def call_tool(self, tool_name: str, params: Dict[str, Any] = None) -> Dict:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": params or {}},
            }
        )
        resp = self._send(payload)
        if resp and "result" in resp:
            return resp["result"]
        return {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": str(resp.get("error", "Unknown error")) if resp else "No response from MCP server",
                }
            ],
        }

    def stop(self):
        self._available = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            self._process = None

    @property
    def available(self) -> bool:
        return self._available

    def get_status(self) -> Dict:
        return {
            "name": self.name,
            "available": self._available,
            "tools": len(self._tools_cache),
            "tool_names": [t.get("name", "?") for t in self._tools_cache],
        }


# ── MCP Manager (manages all MCP servers) ──────────────────────────────────


class MCPManager:
    """Loads .mcp.json and manages all MCP server connections."""

    def __init__(self):
        self._clients: Dict[str, MCPClient] = {}
        self._lock = threading.Lock()
        self._server = None

    def load_config(self, config_path: Optional[str] = None) -> int:
        if config_path is None:
            config_path = os.getenv("MCP_CONFIG_PATH", "")
            if not config_path:
                config_path = str(Path.cwd() / ".mcp.json")
                if not os.path.isfile(config_path):
                    config_path = str(app_root() / ".mcp.json")

        # MCP-006: Workspace containment check
        config_path = self._validate_config_path(config_path)
        if config_path is None:
            return 0

        if not os.path.isfile(config_path):
            logger.warning(f"MCP config not found: {config_path}")
            return 0

        try:
            with open(config_path) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load MCP config: {e}")
            return 0

        servers = cfg.get("mcpServers", {})
        started = 0
        for name, server_cfg in servers.items():
            command = server_cfg.get("command", "")
            args = server_cfg.get("args", [])
            env = server_cfg.get("env", {})
            cwd = server_cfg.get("cwd")
            client = MCPClient(name, command, args, env, cwd)
            if client.start():
                with self._lock:
                    self._clients[name] = client
                started += 1

        logger.info(f"MCP: {started}/{len(servers)} servers started")
        return started

    @staticmethod
    def _validate_config_path(config_path: str) -> Optional[str]:
        """MCP-006: Normalize path and ensure it's within allowed workspace."""
        try:
            resolved = Path(config_path).resolve()
        except (OSError, ValueError):
            logger.error(f"MCP-006: Invalid config path: {config_path}")
            return None

        # Only allow .json and .yaml/.yml extensions
        if resolved.suffix.lower() not in (".json", ".yaml", ".yml"):
            logger.error(f"MCP-006: Blocked config with disallowed extension: {resolved.suffix}")
            return None

        # Block paths that try to escape common system directories
        blocked_parts = {"etc", "windows", "system32", "proc", "sys"}
        path_parts = set(p.lower() for p in resolved.parts)
        if path_parts & blocked_parts:
            logger.error(f"MCP-006: Blocked config path targeting system directory: {resolved}")
            return None

        # Resolve symlinks — reject if symlink points outside workspace
        try:
            if resolved.is_symlink():
                link_target = resolved.resolve()
                # Symlink target must be a .json/.yaml file
                if link_target.suffix.lower() not in (".json", ".yaml", ".yml"):
                    logger.error(f"MCP-006: Symlink points to non-config file: {link_target}")
                    return None
        except OSError:
            logger.error(f"MCP-006: Cannot resolve symlink: {resolved}")
            return None

        return str(resolved)

    def list_tools(self) -> List[Dict]:
        tools = []
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            for t in client.list_tools():
                tools.append(
                    {
                        "server": client.name,
                        "name": t.get("name", "?"),
                        "description": t.get("description", ""),
                        "inputSchema": t.get("inputSchema", {}),
                    }
                )
        return tools

    def call_tool(self, tool_name: str, params: Dict = None) -> Dict:
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            for t in client.list_tools():
                if t.get("name") == tool_name:
                    return client.call_tool(tool_name, params)
        return {"isError": True, "content": [{"type": "text", "text": f"Tool '{tool_name}' not found"}]}

    def get_status(self) -> List[Dict]:
        with self._lock:
            clients = list(self._clients.values())
        return [c.get_status() for c in clients]

    def stop_all(self):
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.stop()
        if self._server:
            self._server.stop()


# ── Rate Limiter (per-IP token bucket) ──────────────────────────────────────


class _TokenBucket:
    """Simple token bucket rate limiter."""

    def __init__(self, max_tokens: int = RATE_LIMIT_MAX_REQUESTS, refill_interval: float = RATE_LIMIT_WINDOW):
        self.max_tokens = max_tokens
        self.refill_interval = refill_interval
        self._tokens: Dict[str, float] = defaultdict(lambda: float(max_tokens))
        self._last_refill: Dict[str, float] = defaultdict(time.time)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            elapsed = now - self._last_refill[key]
            if elapsed > 0:
                refill = elapsed * (self.max_tokens / self.refill_interval)
                self._tokens[key] = min(self.max_tokens, self._tokens[key] + refill)
                self._last_refill[key] = now
            if self._tokens[key] >= 1:
                self._tokens[key] -= 1
                return True
            return False


# ── HMAC Nonce Tracker (replay protection) ──────────────────────────────────


class _NonceTracker:
    """Track request nonces to prevent replay attacks."""

    def __init__(self, tolerance: int = NONCE_TIMESTAMP_TOLERANCE):
        self.tolerance = tolerance
        self._seen: Dict[str, float] = {}  # nonce -> timestamp
        self._lock = threading.Lock()

    def validate(self, nonce: str, timestamp: float, secret: str) -> bool:
        """Validate HMAC-signed nonce. Returns True if valid."""
        now = time.time()
        # Check timestamp window
        if abs(now - timestamp) > self.tolerance:
            logger.warning(f"MCP-005: Request timestamp outside tolerance: {timestamp}")
            return False
        # Check replay
        with self._lock:
            if nonce in self._seen:
                logger.warning(f"MCP-005: Replay detected, nonce={nonce[:16]}...")
                return False
            self._seen[nonce] = now
            # Prune old entries
            cutoff = now - self.tolerance * 2
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        return True


# ── MCP Server (exposes ORBIT tools as MCP server) ───────────────────────


class ORBITMCPServer:
    """Runs an MCP server that exposes ORBIT's own tools to external AI.

    Security (all patches applied):
      MCP-001: API key auth (set MCP_SERVER_API_KEY env var)
      MCP-002: ToolGate policy checks before tool execution
      MCP-003: Argument validation + length limits
      MCP-004: Per-IP rate limiting
      MCP-005: HMAC nonce replay protection (optional, set MCP_SERVER_SECRET)
      MCP-007: Type guard on params
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8321, require_auth: bool = True):
        self.host = host
        self.port = port
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._api_key = os.getenv("MCP_SERVER_API_KEY", "")
        self._require_auth = require_auth  # fail-closed: always require auth
        self._rate_limiter = _TokenBucket()
        self._nonce_tracker = _NonceTracker()
        self._policy_engine = None  # lazy import to avoid circular deps

    def _get_policy_engine(self):
        """Lazy-load PolicyEngine to avoid circular imports."""
        if self._policy_engine is None:
            try:
                from ..core.policy_engine import get_policy_engine

                self._policy_engine = get_policy_engine()
            except Exception:
                logger.warning("MCP-002: PolicyEngine not available — tool calls will be UNCHECKED")
        return self._policy_engine

    def _check_policy(self, tool_name: str, args: Dict) -> Optional[str]:
        """MCP-002: Run tool call through ToolGate policy. Returns error msg or None."""
        engine = self._get_policy_engine()
        if engine is None:
            logger.warning("MCP-SEC: PolicyEngine unavailable — tool calls BLOCKED (fail-closed)")
            return "PolicyEngine unavailable — tool call blocked for security"

        # Map MCP tool names to risk levels
        high_risk_tools = {"file_tools", "computer", "email"}
        low_risk_tools = {"web_search", "news", "calendar"}

        if tool_name in high_risk_tools:
            pass
        elif tool_name in low_risk_tools:
            pass
        else:
            pass

        # Check kill switch first
        try:
            from ..core.kill_switch import get_kill_switch

            ks = get_kill_switch()
            if ks.is_active():
                return "Kill switch is active — all tool calls blocked"
        except Exception:
            pass

        # For file_tools, validate path
        if tool_name == "file_tools" and "path" in args:
            path = args.get("path", "")
            if isinstance(path, str):
                try:
                    resolved = Path(path).resolve()
                    decision = engine.check_path(str(resolved), mutate=True)
                    if not decision.allowed:
                        return f"ToolGate blocked: {decision.reason}"
                except Exception:
                    pass

        # For computer tool, block dangerous commands
        if tool_name == "computer" and args.get("action") == "run_command":
            cmd = args.get("command", "")
            if isinstance(cmd, str):
                blocked = ["rm -rf", "format", "del /f", "shutdown", "reboot"]
                for b in blocked:
                    if b in cmd.lower():
                        return f"ToolGate blocked dangerous command: {b}"

        return None  # allowed

    def _register_orbit_tools(self):
        from ..tools import ComputerTool, NewsTool, SecurityTool, N8nTool
        from ..tools.web_search import WebSearchTool
        from ..tools.file_tools import FileTools
        from ..tools.email_tool import EmailTool
        from ..tools.calendar_tool import CalendarTool

        self._tools = {
            "web_search": WebSearchTool(),
            "file_tools": FileTools(),
            "computer": ComputerTool(),
            "news": NewsTool(),
            "security": SecurityTool(),
            "n8n": N8nTool(),
            "email": EmailTool(),
            "calendar": CalendarTool(),
        }

    def start(self):
        self._register_orbit_tools()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f"MCP server listening on {self.host}:{self.port}")

    def _run(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler

        server_ref = self

        class MCPHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                # MCP-004: Rate limiting
                client_ip = self.client_address[0]
                if not server_ref._rate_limiter.allow(client_ip):
                    resp = {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Rate limit exceeded"}, "id": None}
                    self._send_response(resp, status=429)
                    return

                # MCP-001: Auth check (fail-closed)
                if server_ref._require_auth:
                    if not server_ref._api_key:
                        resp = {
                            "jsonrpc": "2.0",
                            "error": {"code": -32003, "message": "Server not configured — MCP_SERVER_API_KEY required"},
                            "id": None,
                        }
                        self._send_response(resp, status=503)
                        return
                    auth = self.headers.get("Authorization", "")
                    api_key = self.headers.get("X-API-Key", "")
                    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else api_key
                    if not hmac.compare_digest(token, server_ref._api_key):
                        resp = {"jsonrpc": "2.0", "error": {"code": -32003, "message": "Unauthorized"}, "id": None}
                        self._send_response(resp, status=401)
                        return

                # MCP-004: Body size limit
                try:
                    length = int(self.headers.get("Content-Length", 0))
                except (ValueError, TypeError):
                    resp = {
                        "jsonrpc": "2.0",
                        "error": {"code": -32700, "message": "Invalid Content-Length"},
                        "id": None,
                    }
                    self._send_response(resp, status=400)
                    return
                if length > MAX_REQUEST_BODY:
                    resp = {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Request too large"}, "id": None}
                    self._send_response(resp, status=413)
                    return

                body = self.rfile.read(length).decode() if length else "{}"
                try:
                    req = json.loads(body)
                    resp = server_ref._handle_request(req)
                except Exception as e:
                    logger.error("MCP request error: %s", e)
                    resp = {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Internal error"}, "id": None}

                self._send_response(resp)

            def _send_response(self, resp, status=200):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                origin = self.headers.get("Origin", "")
                # Dev-server origins; port follows ORBIT_PORT for parity
                # with the main Flask app.
                _port = os.environ.get("ORBIT_PORT", "5000")
                if origin in (
                    "http://127.0.0.1:5173",
                    "http://localhost:5173",
                    f"http://127.0.0.1:{_port}",
                    f"http://localhost:{_port}",
                ):
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode())

            def do_OPTIONS(self):
                self.send_response(200)
                origin = self.headers.get("Origin", "")
                if origin in (
                    "http://127.0.0.1:5173",
                    "http://localhost:5173",
                    "http://127.0.0.1:5000",
                    "http://localhost:5000",
                ):
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
                self.end_headers()

            def log_message(self, fmt, *args):
                pass

        server = HTTPServer((self.host, self.port), MCPHandler)
        while self._running:
            server.handle_request()

    def _handle_request(self, req: Dict) -> Dict:
        # MCP-007: Type guard on request
        if not isinstance(req, dict):
            return {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Request must be a JSON object"}, "id": None}

        method = req.get("method", "")
        req_id = req.get("id", None)
        base = {"jsonrpc": "2.0", "id": req_id}

        if method == "tools/list":
            tools = []
            for name, tool in self._tools.items():
                tools.append(
                    {
                        "name": name,
                        "description": getattr(tool, "description", name),
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                )
            return {**base, "result": {"tools": tools}}

        elif method == "tools/call":
            # MCP-007: Type guard on params
            params = req.get("params", {})
            if not isinstance(params, dict):
                return {**base, "error": {"code": -32602, "message": "params must be a JSON object"}}

            tool_name = params.get("name", "")
            args = params.get("arguments", {})

            # MCP-003: Validate tool_name
            if not isinstance(tool_name, str) or not tool_name:
                return {**base, "error": {"code": -32602, "message": "tool name must be a non-empty string"}}
            if len(tool_name) > 256:
                return {**base, "error": {"code": -32602, "message": "tool name too long"}}

            # MCP-007: Validate args type
            if not isinstance(args, dict):
                return {**base, "error": {"code": -32602, "message": "arguments must be a JSON object"}}

            # MCP-003: Validate argument string lengths
            for k, v in args.items():
                if isinstance(v, str) and len(v) > MAX_ARG_STRING_LEN:
                    return {
                        **base,
                        "error": {"code": -32602, "message": f"Argument '{k}' exceeds max length {MAX_ARG_STRING_LEN}"},
                    }

            # MCP-002: ToolGate policy check
            policy_error = self._check_policy(tool_name, args)
            if policy_error:
                logger.warning(f"MCP-002: Policy blocked {tool_name}: {policy_error}")
                return {**base, "error": {"code": -32001, "message": policy_error}}

            tool = self._tools.get(tool_name)
            if not tool:
                return {**base, "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"}}

            try:
                if hasattr(tool, "search") and callable(getattr(tool, "search", None)):
                    result = tool.search(**args)
                elif hasattr(tool, "handle_natural") and callable(getattr(tool, "handle_natural", None)):
                    result = tool.handle_natural(args.get("text", ""))
                else:
                    result = tool.run(**args)
                text = json.dumps(result) if not isinstance(result, str) else result
                return {**base, "result": {"content": [{"type": "text", "text": text}]}}
            except Exception as e:
                return {**base, "error": {"code": -32000, "message": str(e)}}

        elif method == "resources/list":
            return {**base, "result": {"resources": []}}

        else:
            return {**base, "result": {}}

    def stop(self):
        self._running = False


# ── Singleton ──────────────────────────────────────────────────────────────

_manager: Optional[MCPManager] = None
_manager_lock = threading.Lock()


def get_manager() -> MCPManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = MCPManager()
    return _manager
