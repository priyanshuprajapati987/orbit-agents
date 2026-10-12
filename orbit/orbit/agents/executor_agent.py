# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
from ..core.llm import legacy_adapter as llm_router
from ..config import ORBIT_SYSTEM_PROMPT
from ..tools import ComputerTool, NewsTool
from .base import BaseAgent

try:
    from ..tools.browser_tool import BrowserTool

    _BROWSER_AVAILABLE = True
except ImportError:
    _BROWSER_AVAILABLE = False

try:
    from ..tools.web_search import WebSearchTool

    _SEARCH_AVAILABLE = True
except ImportError:
    _SEARCH_AVAILABLE = False

try:
    from ..tools.file_tools import FileTools

    _FILE_TOOLS_AVAILABLE = True
except ImportError:
    _FILE_TOOLS_AVAILABLE = False

try:
    from ..tools.github_tool import GitHubTool

    _GH_AVAILABLE = True
except ImportError:
    _GH_AVAILABLE = False

EXECUTOR_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the EXECUTOR AGENT. Execute subtasks from the planner.
Use the appropriate tool for each subtask type:
- type=research: use NewsTool or WebSearchTool
- type=code: generate/analyze code via LLM
- type=file: use FileTools for read/write/edit/list
- type=shell: use ComputerTool for system commands
- type=browser: use BrowserTool for web automation
- type=github: use GitHubTool for repo/issue/PR operations
- type=memory: context-aware response
- type=ask: ask user for clarification
Return concise results."""


class ExecutorAgent(BaseAgent):
    label = "EXE"
    name = "Executor"
    color = "#10b981"
    description = "Executes planned subtasks with full tool chain"

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self.computer_tool = ComputerTool()
        self.news_tool = NewsTool()
        self.browser_tool = BrowserTool() if _BROWSER_AVAILABLE else None
        self.search_tool = WebSearchTool() if _SEARCH_AVAILABLE else None
        self.file_tool = FileTools() if _FILE_TOOLS_AVAILABLE else None
        self.github_tool = GitHubTool() if _GH_AVAILABLE else None

    def can_handle(self, text, intent=None, confidence=0.0):
        return True

    def execute_subtask(self, subtask: dict, context: str = "") -> str:
        task_type = subtask.get("type", "ask")
        description = subtask.get("description", "")
        params = subtask.get("params", {})

        try:
            if task_type == "research":
                if self.search_tool:
                    query = params.get("query", description)
                    result = self.search_tool.search(query, num_results=5)
                    if isinstance(result, dict):
                        items = result.get("results", [])
                        if items:
                            lines = [f"Search results for '{query}':"]
                            for i, r in enumerate(items[:5], 1):
                                lines.append(f"{i}. {r.get('title', '')}")
                                lines.append(f"   {r.get('snippet', '')[:200]}")
                            return "\n".join(lines)
                news_result = self.news_tool.handle_natural(description)
                return str(news_result)

            elif task_type == "code":
                extra = ""
                if params.get("language"):
                    extra = f" Language: {params['language']}."
                if params.get("framework"):
                    extra += f" Framework: {params['framework']}."
                prompt = f"Write code for: {description}.{extra}"
                code = llm_router.generate(prompt, system_prompt=EXECUTOR_PROMPT, force_offline=True)
                lang = params.get("language", "")
                if lang and code:
                    code = f"```{lang}\n{code}\n```"
                return code

            elif task_type == "file":
                if self.file_tool:
                    action = params.get("action", "read")
                    path = params.get("path", "")
                    if action == "write" or action == "save":
                        content = params.get("content", description)
                        if path:
                            result = self.file_tool.write(path, content)
                            return str(result)
                    elif action == "read":
                        if path:
                            result = self.file_tool.read(path)
                            return str(result)
                    elif action == "list":
                        dir_path = path or "."
                        result = self.file_tool.list_dir(dir_path)
                        if isinstance(result, dict):
                            items = result.get("items", [])
                            if isinstance(items, list):
                                return "\n".join(str(e) for e in items[:50])
                            return str(items)
                        return str(result)
                result = self.computer_tool.handle_natural(description)
                return self._extract_output(result)

            elif task_type in ("shell", "system"):
                result = self.computer_tool.handle_natural(description)
                return self._extract_output(result)

            elif task_type == "browser":
                if self.browser_tool and self.browser_tool.is_available():
                    url = params.get("url", description)
                    action = params.get("action", "extract")
                    if action == "screenshot":
                        nav_result = self.browser_tool.navigate(url)
                        if isinstance(nav_result, dict) and "error" in nav_result:
                            return f"Navigation failed: {nav_result['error']}"
                        result = self.browser_tool.screenshot()
                    else:
                        result = self.browser_tool.extract_page(url)
                    return self._extract_output(result)[:3000]
                return "[BrowserTool not available]"

            elif task_type == "github":
                if self.github_tool:
                    action = params.get("action", "")
                    repo = params.get("repo", "")
                    if action == "issues":
                        result = self.github_tool.list_issues(repo)
                    elif action == "prs":
                        result = self.github_tool.list_prs(repo)
                    elif action.startswith("search"):
                        result = self.github_tool.search_code(params.get("query", description))
                    else:
                        result = self.github_tool.handle_natural(description)
                    return str(result)[:2000]
                return "[GitHubTool not available]"

            elif task_type == "memory":
                prompt = f"Context: {context}\nTask: {description}"
                return llm_router.generate(prompt, system_prompt=EXECUTOR_PROMPT, force_offline=True)

            else:
                return f"[Need input] {description}"

        except Exception as e:
            return f"[Executor error in {task_type}: {e}]"

    def _extract_output(self, result):
        if isinstance(result, dict):
            return result.get("stdout", result.get("data", result.get("text", str(result))))
        return str(result)

    def _run(self, text, context=None):
        import re

        tl = text.lower()

        def _hit(*kws):
            return any(re.search(r"\b" + re.escape(k) + r"\b", tl) for k in kws)

        # File ops need an explicit file signal: bare "read"/"delete" misfire
        # on already/thread/spread/deleted, so require a filename, extension,
        # or file keyword nearby.
        if _hit("file", "folder", "directory", "list dir", "write file") or re.search(
            r"\.\w{2,4}\b", tl
        ):
            task_type = "file"
        elif _hit("search", "news", "find", "research"):
            task_type = "research"
        elif _hit("git", "github", "repo", "repository", "issue", "pull request"):
            task_type = "github"
        elif _hit("code", "script", "program"):
            task_type = "code"
        elif _hit("run", "execute", "shell", "command"):
            task_type = "shell"
        elif _hit("browse", "website", "page", "url", "http", "web"):
            task_type = "browser"
        else:
            task_type = "code"
        return self.execute_subtask({"type": task_type, "description": text, "params": {}}, context or "")
