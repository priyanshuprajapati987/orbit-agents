# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""Task and Code agents — system operations, file management, code generation."""

import logging
import os
import re
from pathlib import Path

from ..config import ORBIT_SYSTEM_PROMPT
from ..core.llm import legacy_adapter as llm_router
from .base import BaseAgent

logger = logging.getLogger(__name__)


class TaskAgent(BaseAgent):
    label = "TAS"
    name = "Task Execution"
    color = "#00d4ff"
    description = "System commands, file operations, process management"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the TASK EXECUTION specialist - the hands of the system.

Your capabilities:
- Run system commands (PowerShell, CMD, shell)
- Read, write, edit, copy, delete, move files and folders
- Manage processes (list, kill, start)
- Clipboard operations (copy, paste, get clipboard)
- System info (CPU, RAM, disk, network)
- Take screenshots, control mouse/keyboard
- Open/close/minimize/maximize windows
- Volume, brightness, sleep, shutdown, restart

Rules:
- ALWAYS use FileTools for file operations (read, write, edit, list, search, copy, delete)
- ALWAYS use ComputerTool for system commands and clipboard
- Confirm before destructive operations (delete, overwrite)
- Return structured results: success/failure, file paths, command output
- Keep responses short: state what was done, not how
- If a command fails, suggest a fix or alternative
- For "open X" requests: use the appropriate tool, don't just describe how"""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._tool = None
        self._file_tool = None

    def _get_tool(self):
        if self._tool is None:
            from ..tools import ComputerTool

            self._tool = ComputerTool()
        return self._tool

    def _get_file_tool(self):
        if self._file_tool is None:
            from ..tools.file_tools import FileTools

            self._file_tool = FileTools()
        return self._file_tool

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "run",
            "execute",
            "open",
            "launch",
            "file",
            "folder",
            "process",
            "command",
            "system",
            "clipboard",
            "copy",
            "paste",
            "read file",
            "write file",
            "create file",
            "edit file",
            "mouse",
            "keyboard",
            "click",
            "type",
            "scroll",
            "github",
            "repo",
            "commit",
            "branch",
            "pull request",
            "issue",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if intent == "computer" or intent == "file":
            return score >= 1
        return score >= 2

    def _run(self, text, context=None):
        text_lower = text.lower()

        # ── File creation: check regex FIRST (before file_ops) ──────────────
        # Handles: "Create X with Y", "Write X containing Y", "Make X called Y with Z"
        write_match = re.search(
            r"(?:create|write|make)\s+(?:a\s+)?(?:file\s+)?(?:called\s+|named\s+)?"
            r"[`\"']?([^\s`\"']+)[`\"']?\s+(?:with|containing)\s+[`\"']?(.+?)[`\"']?\s*$",
            text,
            re.I,
        )
        if not write_match:
            # Try: "create X.txt with Y" (with file extension)
            write_match = re.search(
                r"(?:create|write|make)\s+[`\"']?([^\s`\"']+\.(?:txt|py|json|md|csv|log|html|css|js))[`\"']?\s+"
                r"(?:with|containing)\s+[`\"']?(.+?)[`\"']?\s*$",
                text,
                re.I,
            )
        if write_match:
            ft = self._get_file_tool()
            if ft:
                return str(ft.write(write_match.group(1), write_match.group(2)))

        # ── Move file: "move X to Y" ───────────────────────────────────────
        move_match = re.search(
            r"move\s+(?:file\s+)?[`\"']?([^\s`\"']+)[`\"']?\s+(?:to|into)\s+[`\"']?([^\s`\"']+)[`\"']?", text_lower
        )
        if move_match:
            ft = self._get_file_tool()
            if ft:
                return str(ft.move(move_match.group(1), move_match.group(2)))

        # ── Rename file: "rename X to Y" ───────────────────────────────────
        rename_match = re.search(
            r"rename\s+(?:file\s+)?[`\"']?([^\s`\"']+)[`\"']?\s+(?:to|as)\s+[`\"']?([^\s`\"']+)[`\"']?", text_lower
        )
        if rename_match:
            ft = self._get_file_tool()
            if ft:
                return str(ft.move(rename_match.group(1), rename_match.group(2)))

        # ── Append: "Append 'X' to the file Y" / "Add 'X' to Y.txt" ─────────
        append_match = re.search(
            r"\b(?:append|add)\s+[`\"']([^`\"']+)[`\"']\s+to\s+(?:the\s+)?(?:file\s+)?[`\"']?([^\s`\"']+)[`\"']?",
            text,
            re.I,
        )
        if not append_match:
            append_match = re.search(
                r"\b(?:append|add)\s+(.+?)\s+to\s+(?:the\s+)?(?:file\s+)?([^\s`\"']+\.[a-z0-9]+)",
                text,
                re.I,
            )
        if append_match:
            ft = self._get_file_tool()
            if ft:
                return str(ft.append(append_match.group(2), append_match.group(1)))

        # ── Read file by path: "Read X.txt and tell me ..." ─────────────────
        read_path_match = re.search(
            r"\bread\s+(?:the\s+)?(?:file\s+)?[`\"']?([^\s`\"']+\.(?:txt|py|json|md|csv|log|html|css|js))[`\"']?",
            text,
            re.I,
        )
        if read_path_match:
            ft = self._get_file_tool()
            if ft:
                result = ft.read(read_path_match.group(1))
                if isinstance(result, dict):
                    if "error" in result:
                        return f"Error: {result['error']}"
                    return str(result.get("content", ""))[:2000]
                return str(result)[:2000]

        # ── Delete file by path: "Delete the file X.txt" ────────────────────
        del_match = re.search(
            r"\bdelete\s+(?:the\s+)?(?:file\s+)?[`\"']?([^\s`\"']+?\.[a-z0-9]+|[^\s`\"']+[/\\][^\s`\"']+)[`\"']?",
            text,
            re.I,
        )
        if del_match:
            ft = self._get_file_tool()
            if ft:
                return str(ft.delete(del_match.group(1)))

        # ── List files: "List the files in the orbit directory" ─────────────
        # Tolerate adjectives between verb and noun ("List all Python files")
        # and stop the path phrase at connectors ("in X and count ...").
        list_match = re.search(
            r"\b(?:list|show)\s+(?:the\s+)?(?:all\s+)?(?:\w+\s+){0,3}?(?:files?|dir(?:ectory|s)?|folder)"
            r"(?:\s+(?:in|from|inside)\s+(?:the\s+)?(.+?)(?=\s+(?:and|that|with|please|then)\b|$))?",
            text,
            re.I,
        )
        if list_match:
            path = (list_match.group(1) or ".").strip().strip("`\"'.,;:")
            path = re.sub(r"^(?:the\s+)+", "", path).strip()
            path = re.sub(r"\s+(?:directory|dir|folder)$", "", path).strip()
            if not path or re.fullmatch(r"(?:current|this|working)", path, re.I):
                path = "."
            ft = self._get_file_tool()
            if ft:
                result = ft.list_dir(path)
                if isinstance(result, dict):
                    if "error" in result:
                        return f"Error: {result['error']}"
                    items = result.get("items", [])
                    display = result.get("path", path)
                    tl = text.lower()
                    # Typed/count-aware summary: "List all Python files and
                    # count how many" must answer with a count, not a dump.
                    type_map = [
                        ("python", ".py", "Python"), ("javascript", ".js", "JavaScript"),
                        ("js", ".js", "JavaScript"), ("json", ".json", "JSON"),
                        ("csv", ".csv", "CSV"), ("markdown", ".md", "Markdown"),
                        ("md", ".md", "Markdown"), ("text", ".txt", "Text"),
                        ("txt", ".txt", "Text"), ("html", ".html", "HTML"),
                        ("css", ".css", "CSS"), ("log", ".log", "Log"),
                    ]
                    if "files" in tl:
                        for kw, suffix, label in type_map:
                            if re.search(rf"\b{re.escape(kw)}\b", tl):
                                matched = [
                                    i for i in items
                                    if str(i.get("name", "")).lower().endswith(suffix)
                                ]
                                names = [f"  {i.get('name', '?')}" for i in matched[:30]]
                                return (
                                    f"Found {len(matched)} {label} files in {display}:\n"
                                    + "\n".join(names)
                                )
                    names = [f"  {i.get('name', '?')}{'/' if i.get('type') == 'dir' else ''}" for i in items[:30]]
                    py = sum(1 for i in items if str(i.get("name", "")).lower().endswith(".py"))
                    return (
                        f"Files in {display} ({result.get('count', len(items))} items, {py} Python files):\n"
                        + "\n".join(names)
                    )
                return str(result)[:2000]

        # ── Search files: "Search for Python files in the orbit directory" ──
        search_match = re.search(
            r"\bsearch\s+(?:for\s+)?(\S+)?\s*files?\s+(?:in|inside|from)\s+(?:the\s+)?(.+?)\s*$",
            text,
            re.I,
        )
        if search_match:
            kw = (search_match.group(1) or "").strip("`\"'")
            raw = search_match.group(2).strip().strip("`\"'")
            raw = re.sub(r"^(?:the\s+)+", "", raw).strip()
            raw = re.sub(r"\s+(?:directory|dir|folder)$", "", raw).strip() or "."
            glob_map = {
                "python": "*.py", "py": "*.py", "javascript": "*.js", "js": "*.js",
                "json": "*.json", "markdown": "*.md", "md": "*.md", "text": "*.txt",
                "txt": "*.txt", "csv": "*.csv", "html": "*.html", "css": "*.css", "log": "*.log",
            }
            if kw.lower() in glob_map:
                pattern = glob_map[kw.lower()]
            elif kw and ("*" in kw or "." in kw):
                pattern = kw
            else:
                pattern = f"*{kw}*" if kw else "*"
            ft = self._get_file_tool()
            if ft:
                result = ft.search(raw, pattern)
                if isinstance(result, dict):
                    if "error" in result:
                        return f"Error: {result['error']}"
                    matches = result.get("matches", [])
                    if not matches:
                        return f"No files matching '{pattern}' in {raw}"
                    lines = [f"  {m.get('path', '?')}" for m in matches[:20]]
                    return (
                        f"Found {result.get('count', len(matches))} files (pattern {pattern} in {raw}):\n"
                        + "\n".join(lines)
                    )
                return str(result)[:2000]

        # ── File operations: keyword-based detection ─────────────────────────
        file_ops = [
            "read file",
            "write file",
            "create file",
            "create a file",
            "edit file",
            "list files",
            "list directory",
            "show files",
            "file info",
            "copy file",
            "delete file",
            "search files",
            "read pdf",
            "make a file",
            "save to file",
            "save file",
        ]
        if any(op in text_lower for op in file_ops):
            ft = self._get_file_tool()
            if "read pdf" in text_lower:
                m = re.search(r"read\s+pdf\s+[`\"']?([^\s`\"']+)", text, re.IGNORECASE)
                if m:
                    result = ft.read_pdf(m.group(1))
                    if isinstance(result, dict):
                        return result.get("content", str(result))[:3000]
                    return str(result)[:3000]
            if "read file" in text_lower or "show file" in text_lower:
                m = re.search(r"(?:read|show)\s+(?:file\s+)?[`\"']?([^\s`\"']+)", text, re.IGNORECASE)
                if m:
                    result = ft.read(m.group(1))
                    if isinstance(result, dict):
                        return result.get("content", str(result))[:2000]
                    return str(result)[:2000]
            elif "list files" in text_lower or "list directory" in text_lower or "show files" in text_lower:
                m = re.search(r"(?:list|show)\s+(?:files?\s+(?:in|from)\s+)?[`\"']?([^\s`\"']+)", text, re.IGNORECASE)
                path = m.group(1) if m else "."
                result = ft.list_dir(path)
                if isinstance(result, dict):
                    items = result.get("items", [])
                    names = [i.get("name", "?") for i in items[:20]]
                    return f"Files in {result.get('path', path)}:\n" + "\n".join(f"  {n}" for n in names)
                return str(result)[:2000]
            elif "file info" in text_lower:
                m = re.search(r"(?:file\s+info\s+)([`\"']?[^\s`\"']+)", text, re.IGNORECASE)
                if m:
                    result = ft.info(m.group(1))
                    if isinstance(result, dict):
                        return f"{Path(result.get('path', '?')).name} — {result.get('type', '?')}, {result.get('size', 0)} bytes"
                    return str(result)[:2000]
            elif "copy file" in text_lower:
                # Try quoted first: copy file 'src' to 'dst'
                parts = re.findall(r"[`\"']([^`\"']+)[`\"']", text)
                if len(parts) >= 2:
                    return str(ft.copy(parts[0], parts[1]))
                # Try unquoted: copy file src to dst
                copy_match = re.search(
                    r"copy\s+file\s+[`\"']?([^\s`\"']+)[`\"']?\s+to\s+[`\"']?([^\s`\"']+)[`\"']?", text
                )
                if copy_match:
                    return str(ft.copy(copy_match.group(1), copy_match.group(2)))
                return "Usage: copy file 'source' to 'destination'"
            elif "delete file" in text_lower:
                m = re.search(r"delete\s+(?:file\s+)?[`\"']?([^\s`\"']+)", text, re.IGNORECASE)
                if m:
                    # ToolGate (inside ft.delete) is the safety layer — a
                    # confirmation string was a dead end (nothing consumed it).
                    return str(ft.delete(m.group(1)))
            elif "search files" in text_lower:
                m = re.search(r"search\s+files?\s+(?:for\s+)?[`\"']?([^\s`\"']+)", text, re.IGNORECASE)
                pattern = m.group(1) if m else "*"
                return str(ft.search(".", pattern))
            elif (
                "write file" in text_lower
                or "create file" in text_lower
                or "create a file" in text_lower
                or "make a file" in text_lower
            ):
                # Quoted strings: write file 'path' with 'content'
                parts = re.findall(r"[`\"']([^`\"']+)[`\"']", text)
                if len(parts) >= 2:
                    return str(ft.write(parts[0], parts[1]))
                return "Usage: write file 'path' with 'content'"
            elif "edit file" in text_lower:
                return "Edit requires old_text and new_text. Use: edit file 'path' replace 'old' with 'new'"

        # ── Clipboard: explicit branch (IP regex below used to steal these
        #    — "clipboard" contains the substring "ip") ──────────────────────
        if "clipboard" in text_lower:
            tool = self._get_tool()
            if tool:
                result = tool.handle_natural(text)
                if isinstance(result, dict):
                    if result.get("success"):
                        # pyperclip path returns "text"; PowerShell fallback
                        # returns "stdout".
                        val = str(result.get("text") or result.get("stdout") or "")
                        if "copy" in text_lower or "set" in text_lower or "write" in text_lower:
                            return f"Clipboard set: {val}"
                        return f"Clipboard: {val}"
                    return f"Error: {result.get('error', 'unknown')}"
                return str(result)[:1000]

        # ── IP address query ────────────────────────────────────────────────
        # Word boundaries required: bare "ip" matches cl/ipboard etc.
        ip_match = re.search(
            r"\b(?:what(?:'s|\s+is)\s+)?(?:my\s+)?(?:ip(?:\s+address)?|local\s+ip|public\s+ip)\b", text_lower
        )
        if ip_match:
            tool = self._get_tool()
            if tool:
                result = tool.run_command("ipconfig")
                if isinstance(result, dict) and result.get("success"):
                    stdout = result.get("stdout", "")
                    # Extract IPv4 address
                    for line in stdout.split("\n"):
                        if "IPv4" in line:
                            return f"Your IP: {line.split(':')[-1].strip()}"
                    return stdout[:1000]
                return str(result)[:1000]

        # ── CuaDriver commands ───────────────────────────────────────────
        if "cua" in text_lower or "computer use" in text_lower or "vision click" in text_lower:
            from ..tools.cua_computer import CuaComputerTool, is_available

            if not is_available():
                return "cua-driver not running. Start with: `cua-driver serve`"
            cc = CuaComputerTool()
            if "screenshot" in text_lower or "screen" in text_lower:
                r = cc.get_desktop_state("orbit")
                if "error" in r:
                    return f"Error: {r['error']}"
                return (
                    f"**Desktop state captured** — {r.get('element_count', 0)} elements\n"
                    f"Tree preview:\n```\n{r.get('tree_markdown', '')[:2000]}\n```"
                )
            if "apps" in text_lower or "list_apps" in text_lower:
                r = cc.list_apps()
                if "error" in r:
                    return f"Error: {r['error']}"
                apps = r.get("apps", [])
                return "\n".join(
                    f"  {a.get('name', '?')} (pid={a.get('pid')}) {'🟢' if a.get('running') else '⚪'}"
                    for a in apps[:10]
                )
            if "click" in text_lower:
                m = re.search(r"click\s+(\d+)", text_lower)
                el = int(m.group(1)) if m else None
                pid_m = re.search(r"pid\s*(\d+)", text_lower)
                pid = int(pid_m.group(1)) if pid_m else None
                if el is not None and pid:
                    r = cc.click(pid=pid, element_index=el)
                    return f"Clicked element [{el}]: {r.get('status', 'ok')}"
                return "Usage: 'cua click <element_index> pid <pid>' or 'cua screenshot'"
            if "type" in text_lower:
                m = re.search(r"type\s+(.+?)(?:\s+pid\s+(\d+))?", text_lower)
                txt = m.group(1) if m else ""
                pid_m = re.search(r"pid\s*(\d+)", text_lower)
                pid = int(pid_m.group(1)) if pid_m else None
                if txt and pid:
                    r = cc.type_text(pid=pid, text=txt)
                    return f"Typed '{txt}': {r.get('status', 'ok')}"
                return "Usage: 'cua type <text> pid <pid>'"
            return (
                "Cua commands: `cua screenshot`, `cua apps`, `cua click <index> pid <pid>`, `cua type <text> pid <pid>`"
            )

        tool = self._get_tool()
        if not tool:
            return "Task agent unavailable."
        result = tool.handle_natural(text)
        if isinstance(result, dict):
            if result.get("success"):
                return str(result.get("stdout", result.get("data", str(result))))[:1000]
            # Fallback to DesktopAutomationTool — but NEVER for file/code
            # prompts: its "type|write|enter" branch would type them on the
            # focused window instead of writing files (E6 tool_007 bug).
            _looks_like_file_op = re.search(
                r"\b(?:file|folder|directory|read|delete|append|list)\b"
                r"|\bsave\s+(?:it\s+|this\s+)?to\s+\S+\.[a-z0-9]+\b"
                r"|\bwrite\s+(?:a\s+|an\s+)?(?:python|javascript|function|script|code)\b"
                r"|\.(?:txt|py|json|md|csv|log)\b",
                text_lower,
            )
            if not _looks_like_file_op:
                try:
                    from ..tools.desktop import DesktopAutomationTool

                    dt = DesktopAutomationTool()
                    if dt.is_available():
                        dresult = dt.handle_natural(text)
                        if isinstance(dresult, dict) and dresult.get("success"):
                            return str(dresult)[:1000]
                except Exception:
                    pass
            # Fallback to GitHubTool
            try:
                from ..tools.github_tool import GitHubTool

                gt = GitHubTool()
                if gt.is_configured():
                    gresult = gt.handle_natural(text)
                    if isinstance(gresult, dict) and gresult.get("success"):
                        return str(gresult)[:1000]
            except Exception:
                pass
            return f"Error: {result.get('error', 'unknown')}"
        return str(result)[:1000]


class CodeAgent(BaseAgent):
    label = "COD"
    name = "Code"
    color = "#7c3aed"
    description = "Code generation, debugging, analysis"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the CODE specialist - the architect of solutions.

Your capabilities:
- Write clean, working code in any language (Python, JS, TS, Java, C++, Go, Rust, SQL, etc.)
- Debug code: find bugs, suggest fixes, explain errors
- Refactor: improve readability, performance, maintainability
- Review code: catch issues, suggest improvements, check for security
- Explain code: break down complex logic, document functions
- Build APIs: REST, GraphQL, WebSocket endpoints
- Create components: React, Vue, Angular, Flutter widgets
- Database: schema design, queries, migrations

Rules:
- ALWAYS provide complete, runnable code - never placeholders or pseudocode
- Include imports and dependencies at the top
- Add brief comments for non-obvious logic
- Handle edge cases: null checks, error handling, input validation
- Follow language conventions (PEP 8 for Python, ESLint for JS)
- When debugging: explain the root cause before showing the fix
- For "build X" requests: create the actual file, not just code snippets
- If unsure about requirements, ask for clarification before coding"""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._completion_engine = None

    def _get_completion_engine(self):
        if self._completion_engine is None:
            from ..tools.code_completion import CodeCompletionEngine

            self._completion_engine = CodeCompletionEngine()
        return self._completion_engine

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "code",
            "write code",
            "program",
            "script",
            "debug",
            "function",
            "class",
            "python",
            "javascript",
            "algorithm",
            "api",
            "bug",
            "error",
            "complete",
            "suggest",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if intent == "chat" and confidence < 0.5:
            return score >= 2
        return score >= 1

    def _run(self, text, context=None):
        text_lower = text.lower()
        if "complete" in text_lower or "suggest" in text_lower or "next line" in text_lower:
            m = re.search(r"(?:complete|suggest|next\s+line\s+(?:for|in))\s+[`\"']?([^\s`\"']+)", text, re.IGNORECASE)
            file_path = m.group(1) if m else None
            engine = self._get_completion_engine()
            if file_path and os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                suggestions = engine.suggest_completions(file_path, len(lines), 0, lines)
                if suggestions:
                    return "Code suggestions:\n" + "\n".join(f"  - {s.get('text', str(s))}" for s in suggestions[:5])
            return "Provide a file path for code completion, or paste code for suggestions."

        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        response = llm_router.generate(text, history=self.history, system_prompt=sys_prompt)

        blocks: list[str] = []
        if response and not response.startswith("[Error"):
            try:
                from ..core.code_reviewer import (
                    CodeReviewer,
                    extract_code_blocks,
                    verify_python_syntax,
                )

                reviewer = CodeReviewer()
                blocks = extract_code_blocks(response)
                code_for_review = "\n\n".join(blocks) if blocks else response
                review = reviewer.review_code(code_for_review, language="python")
                issues = review.get("issues", [])
                if issues:
                    issue_text = "\n".join(f"  - [{i.get('severity', '?')}] {i.get('message', '')}" for i in issues[:5])
                    response += f"\n\n**Code Review ({len(issues)} issues found):**\n{issue_text}"

                # Syntax-verify generated Python and attempt one auto-fix.
                for blk in blocks:
                    ok, err = verify_python_syntax(blk)
                    if not ok:
                        response += f"\n\n**⚠️ Syntax check failed:** `{err}`"
                        try:
                            fixed = llm_router.generate(
                                f"Fix the Python syntax error below. Return ONLY the corrected code "
                                f"inside a single ```python fence.\nError: {err}\n\n{blk}",
                                system_prompt="You are a code repair expert. Output only valid Python code.",
                            )
                            if fixed and not fixed.startswith("[Error"):
                                fblocks = extract_code_blocks(fixed)
                                fcode = fblocks[0] if fblocks else fixed
                                ok2, _ = verify_python_syntax(fcode)
                                if ok2:
                                    response += f"\n\n**Auto-fixed code:**\n```python\n{fcode}\n```"
                        except Exception:
                            pass
                        break
            except Exception:
                pass

        save_match = None
        for pattern in [
            r"save\s+to\s+[`\"']?([^\s`\"']+)",
            r"save\s+(?:it|this|that)\s+to\s+[`\"']?([^\s`\"']+)",
            r"write\s+to\s+[`\"']?([^\s`\"']+)",
            r"export\s+as\s+[`\"']?([^\s`\"']+)",
        ]:
            save_match = re.search(pattern, text, re.IGNORECASE)
            if save_match:
                break
        if save_match and response and not response.startswith("[Error"):
            file_path = save_match.group(1)
            try:
                from ..core.code_reviewer import extract_code_blocks as _xc
                from ..tools.file_tools import FileTools

                content_to_save = _xc(response)[0] if _xc(response) else response
                ft = FileTools()
                ft.write(file_path, content_to_save)
                response += f"\n\n**Saved to:** `{file_path}`"
            except Exception as e:
                response += f"\n\n**Save failed:** {e}"

        return response
