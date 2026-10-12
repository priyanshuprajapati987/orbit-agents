# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
from ..core.runtime_paths import app_root
import json
import os
import re
import subprocess
import time
from urllib.parse import urlparse

import requests


class N8nTool:
    # SSRF protection: only allow these URL patterns
    _SAFE_HOSTS = re.compile(r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|::1|localhost\.localdomain)$")

    def __init__(self):
        self.base_url = "http://localhost:5678"
        self.api_key = ""
        self._process = None
        self._load_config()

    def _validate_url(self, url: str) -> str:
        """Validate URL is safe (no SSRF). Returns cleaned URL or raises ValueError."""
        try:
            parsed = urlparse(url)
        except Exception:
            raise ValueError(f"Invalid URL: {url!r}")
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"URL must use http/https: {url!r}")
        hostname = parsed.hostname or ""
        if not self._SAFE_HOSTS.match(hostname):
            raise ValueError(f"SSRF blocked: {url!r} — only localhost/127.0.0.1 allowed")
        return url

    def _load_config(self):
        config_file = app_root() / "n8n_config.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    cfg = json.load(f)
                    url = cfg.get("n8n_url", self.base_url)
                    try:
                        self._validate_url(url)
                        self.base_url = url
                    except ValueError:
                        pass  # Keep default
                    # Warn if config still has key (breaking change: env-only since v3.7.0)
                    if cfg.get("n8n_api_key"):
                        import warnings

                        warnings.warn(
                            "n8n_config.json 'n8n_api_key' is ignored. Set N8N_API_KEY environment variable instead.",
                            DeprecationWarning,
                            stacklevel=2,
                        )
                    self.api_key = os.environ.get("N8N_API_KEY", "")
            except (json.JSONDecodeError, OSError):
                pass
        if not self.api_key:
            self.api_key = os.getenv("N8N_API_KEY", "")
        env_url = os.getenv("N8N_BASE_URL", "")
        if env_url:
            try:
                self._validate_url(env_url)
                self.base_url = env_url
            except ValueError:
                pass  # Keep current

    def save_config(self, url: str = None, api_key: str = None):
        if url:
            try:
                self._validate_url(url)
            except ValueError as e:
                return {"success": False, "error": str(e)}
        config_file = app_root() / "n8n_config.json"
        cfg = {"n8n_url": url or self.base_url, "n8n_api_key": api_key or self.api_key}
        with open(config_file, "w") as f:
            json.dump(cfg, f, indent=2)
        if url:
            self.base_url = url
        if api_key:
            self.api_key = api_key

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-N8N-API-KEY"] = self.api_key
        return h

    def check_status(self) -> dict:
        try:
            r = requests.get(f"{self.base_url}/healthz", timeout=5)
            return {"online": r.status_code == 200, "status_code": r.status_code, "url": self.base_url}
        except requests.ConnectionError:
            return {
                "online": False,
                "error": f"n8n not reachable at {self.base_url}. Install: npm install -g n8n",
                "url": self.base_url,
            }

    def start_n8n(self) -> str:
        if self._process is not None and self._process.poll() is None:
            return "n8n already running."
        try:
            import shutil

            n8n_path = shutil.which("n8n") or shutil.which("n8n.cmd")
            if not n8n_path:
                return "n8n command not found. Install it: npm install -g n8n"
            self._process = subprocess.Popen(
                [n8n_path, "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            for _ in range(30):
                time.sleep(1)
                status = self.check_status()
                if status.get("online"):
                    return "n8n started successfully! Web UI: http://localhost:5678"
            return "n8n starting... check http://localhost:5678 after a moment."
        except Exception as e:
            return f"Failed to start n8n: {e}"

    def list_workflows(self) -> list:
        try:
            r = requests.get(f"{self.base_url}/rest/workflows", headers=self._headers(), timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data.get("data", []) if isinstance(data, dict) else []
            return []
        except Exception:
            return []

    def get_workflow(self, workflow_id: str) -> dict:
        try:
            r = requests.get(f"{self.base_url}/rest/workflows/{workflow_id}", headers=self._headers(), timeout=10)
            if r.status_code == 200:
                return r.json().get("data", {})
            return {}
        except Exception:
            return {}

    def activate_workflow(self, workflow_id: str) -> dict:
        try:
            r = requests.post(
                f"{self.base_url}/rest/workflows/{workflow_id}/activate",
                headers=self._headers(),
                timeout=10,
            )
            return {"success": r.status_code == 200, "status": r.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def deactivate_workflow(self, workflow_id: str) -> dict:
        try:
            r = requests.post(
                f"{self.base_url}/rest/workflows/{workflow_id}/deactivate",
                headers=self._headers(),
                timeout=10,
            )
            return {"success": r.status_code == 200, "status": r.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def trigger_webhook(self, webhook_id: str, payload: dict = None) -> dict:
        try:
            r = requests.post(
                f"{self.base_url}/webhook/{webhook_id}",
                json=payload or {},
                timeout=15,
            )
            return {"success": r.status_code < 400, "status": r.status_code, "data": r.text[:500]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def trigger_workflow_by_name(self, name: str, payload: dict = None) -> str:
        workflows = self.list_workflows()
        match = None
        for w in workflows:
            if name.lower() in w.get("name", "").lower():
                match = w
                break
        if not match:
            available = "\n".join([f"  • {w.get('name', 'Unnamed')}" for w in workflows[:10]])
            return (
                f"Workflow '{name}' not found. Available:\n{available}"
                if available
                else f"Workflow '{name}' not found. No workflows available."
            )
        wf_id = match.get("id")
        wf_name = match.get("name", "Unnamed")
        webhook_id = None
        nodes = match.get("nodes", [])
        for node in nodes:
            if node.get("type", "").lower() == "n8n-nodes-base.webhook":
                webhook_id = node.get("parameters", {}).get("path", node.get("parameters", {}).get("httpMethod", ""))
                break
        if not webhook_id:
            webhook_ids = []
            for node in nodes:
                params = node.get("parameters", {})
                if "path" in params:
                    webhook_ids.append(params["path"])
            if webhook_ids:
                results = []
                for wid in webhook_ids:
                    res = self.trigger_webhook(wid, payload)
                    results.append(
                        f"  Webhook '{wid}': {'✅' if res['success'] else '❌'} {res.get('data', res.get('error', ''))[:100]}"
                    )
                return f"Triggered workflow '{wf_name}':\n" + "\n".join(results)
        if webhook_id:
            res = self.trigger_webhook(webhook_id, payload)
            ok = "✅" if res["success"] else "❌"
            return f"Workflow '{wf_name}' triggered via webhook '{webhook_id}': {ok}\n{res.get('data', res.get('error', ''))[:200]}"
        return f"Workflow '{wf_name}' found (ID: {wf_id}) but no webhook trigger node found. Open web UI to trigger manually."

    def handle_natural(self, text: str) -> str:
        text_lower = text.lower()
        status = self.check_status()

        if "start" in text_lower and "n8n" in text_lower:
            return self.start_n8n()

        if not status.get("online"):
            return "n8n is offline. Install with `npm install -g n8n` then start it.\nOr go to https://n8n.io for cloud version."

        if "open" in text_lower or "browser" in text_lower or "web" in text_lower or "ui" in text_lower:
            import webbrowser

            webbrowser.open(self.base_url)
            return f"n8n Web UI browser mein khol diya: {self.base_url}"

        if "status" in text_lower or "health" in text_lower:
            return f"n8n: Online ✅ at {self.base_url}"

        if "workflow" in text_lower and ("list" in text_lower or "show" in text_lower or "all" in text_lower):
            workflows = self.list_workflows()
            if not workflows:
                return "No workflows found. Create one in the n8n web UI."
            lines = [f"📋 {len(workflows)} workflow(s):"]
            for w in workflows[:20]:
                name = w.get("name", "Unnamed")
                active = "🟢" if w.get("active") else "⚪"
                lines.append(f"  {active} {name} (ID: {w.get('id', 'N/A')})")
            return "\n".join(lines)

        if "run" in text_lower or "trigger" in text_lower or "execute" in text_lower:
            import re

            name_match = re.search(
                r"(?:run|trigger|execute)\s+(\w[\w\s-]*?)(?:\s+with\s+)?(?:data|payload|input)?(?:\s*:)?",
                text_lower,
                re.IGNORECASE,
            )
            if name_match:
                name = name_match.group(1).strip()
                payload = {}
                json_match = re.search(r"\{.*\}", text, re.DOTALL)
                if json_match:
                    try:
                        payload = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        payload = {"raw": text}
                elif "with" in text_lower:
                    after = text.split("with", 1)[-1].strip()
                    if after and not after.startswith("{"):
                        payload = {"input": after}
                return self.trigger_workflow_by_name(name, payload)
            workflows = self.list_workflows()
            if workflows:
                names = "\n".join([f"  • {w.get('name', 'Unnamed')}" for w in workflows[:10]])
                return f"Which workflow? Available:\n{names}\n\nSay: 'run workflow name' or 'trigger workflow name with data'"
            return "No workflows found to run."

        if "activate" in text_lower or "deactivate" in text_lower:
            import re

            action = "activate" if "activate" in text_lower else "deactivate"
            name_match = re.search(r"(?:activate|deactivate)\s+(\w[\w\s-]*)", text_lower)
            if name_match:
                name = name_match.group(1).strip()
                workflows = self.list_workflows()
                for w in workflows:
                    if name.lower() in w.get("name", "").lower():
                        wid = w.get("id")
                        wname = w.get("name", "Unnamed")
                        if action == "activate":
                            res = self.activate_workflow(wid)
                        else:
                            res = self.deactivate_workflow(wid)
                        ok = "✅" if res["success"] else "❌"
                        return f"Workflow '{wname}' {action}d: {ok}"
                return f"Workflow '{name}' not found."

        return "I can: check status, list workflows, run/trigger workflows with data, activate/deactivate workflows. What do you need?"
