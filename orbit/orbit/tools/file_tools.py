# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT File Tools

Safe file operations with confirmation for destructive actions.
Supports: read, write, edit, list, search, copy, move, delete.
"""

import shutil
from pathlib import Path
from typing import Dict, Optional
from ..logging_config import get_logger
from ..core.policy_engine import check_tool

logger = get_logger(__name__)

BLOCKED_DIRS = {"System32", "SysWOW64", "Windows", ".git", "node_modules", "__pycache__"}
# Lowercased copy: _is_safe_path compares part.lower() against this set.
# Comparing against the mixed-case set above can never match (sandbox dead).
_BLOCKED_DIRS_LOWER = {b.lower() for b in BLOCKED_DIRS}


class FileTools:
    def __init__(self):
        self.name = "file_tools"
        self.description = "Read, write, edit, and manage files safely"
        self.requires_approval = {"write", "edit", "delete", "move"}

    def _is_safe_path(self, path: str) -> bool:
        try:
            p = Path(path).resolve()
            for part in p.parts:
                if part.lower() in _BLOCKED_DIRS_LOWER:
                    return False
            return True
        except Exception:
            return False

    def _gate(self, action: str, path: str, mutate: bool = True) -> Optional[Dict]:
        """Central policy gate. Returns error dict when blocked, else None."""
        decision = check_tool("file", action, path=path, mutate=mutate)
        if not decision.allowed:
            return {"error": f"Access denied ({decision.reason}): {path}", "policy": decision.to_dict()}
        return None

    def read(self, path: str, encoding: str = "utf-8") -> Dict:
        blocked = self._gate("read", path, mutate=False)
        if blocked:
            return blocked
        try:
            p = Path(path)
            if not p.exists():
                return {"error": f"File not found: {path}"}
            if p.stat().st_size > 1_000_000:
                return {"error": "File too large (>1MB). Use specific line range."}
            content = p.read_text(encoding=encoding, errors="replace")
            return {"content": content, "path": str(p), "size": p.stat().st_size, "lines": content.count("\n") + 1}
        except Exception as e:
            return {"error": str(e)}

    def write(self, path: str, content: str, encoding: str = "utf-8") -> Dict:
        if not self._is_safe_path(path):
            return {"error": "Access denied: writing to blocked directory"}
        blocked = self._gate("write", path)
        if blocked:
            return blocked
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding=encoding)
            return {"success": True, "path": str(p), "bytes_written": len(content.encode(encoding))}
        except Exception as e:
            return {"error": str(e)}

    def append(self, path: str, content: str, encoding: str = "utf-8") -> Dict:
        if not self._is_safe_path(path):
            return {"error": "Access denied: appending to blocked directory"}
        blocked = self._gate("write", path)
        if blocked:
            return blocked
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            old = p.read_text(encoding=encoding, errors="replace") if p.exists() else ""
            sep = "" if (not old or old.endswith("\n")) else "\n"
            suffix = "" if content.endswith("\n") else "\n"
            p.write_text(old + sep + content + suffix, encoding=encoding)
            return {"success": True, "path": str(p), "appended": len(content.encode(encoding))}
        except Exception as e:
            return {"error": str(e)}

    def edit(self, path: str, old_text: str, new_text: str, encoding: str = "utf-8") -> Dict:
        if not self._is_safe_path(path):
            return {"error": "Access denied: editing in blocked directory"}
        blocked = self._gate("edit", path)
        if blocked:
            return blocked
        try:
            p = Path(path)
            if not p.exists():
                return {"error": f"File not found: {path}"}
            content = p.read_text(encoding=encoding)
            if old_text not in content:
                return {"error": "old_text not found in file"}
            count = content.count(old_text)
            new_content = content.replace(old_text, new_text)
            p.write_text(new_content, encoding=encoding)
            return {"success": True, "path": str(p), "replacements": count}
        except Exception as e:
            return {"error": str(e)}

    def list_dir(self, path: str = ".", show_hidden: bool = False) -> Dict:
        blocked = self._gate("list_dir", path, mutate=False)
        if blocked:
            return blocked
        try:
            p = Path(path)
            if not p.exists():
                return {"error": f"Directory not found: {path}"}
            if not p.is_dir():
                return {"error": f"Not a directory: {path}"}
            items = []
            for item in sorted(p.iterdir()):
                if not show_hidden and item.name.startswith("."):
                    continue
                items.append(
                    {
                        "name": item.name,
                        "type": "dir" if item.is_dir() else "file",
                        "size": item.stat().st_size if item.is_file() else 0,
                        "modified": item.stat().st_mtime,
                    }
                )
            return {"path": str(p), "items": items, "count": len(items)}
        except Exception as e:
            return {"error": str(e)}

    def search(self, path: str, pattern: str, max_results: int = 50, max_files: int = 5000) -> Dict:
        blocked = self._gate("search", path, mutate=False)
        if blocked:
            return blocked
        try:
            p = Path(path)
            if not p.exists():
                return {"error": f"Path not found: {path}"}
            matches = []
            files_scanned = 0
            limit_hit = False
            for match in p.rglob(pattern):
                files_scanned += 1
                if files_scanned > max_files:
                    limit_hit = True
                    break
                if len(matches) < max_results:
                    matches.append(
                        {
                            "path": str(match),
                            "type": "dir" if match.is_dir() else "file",
                            "size": match.stat().st_size if match.is_file() else 0,
                        }
                    )
            result = {"pattern": pattern, "matches": matches, "count": len(matches), "files_scanned": files_scanned}
            if limit_hit:
                result["warning"] = f"Scan stopped after {max_files} files. Results may be incomplete."
            return result
        except Exception as e:
            return {"error": str(e)}

    def copy(self, source: str, destination: str) -> Dict:
        if not self._is_safe_path(source):
            return {"error": "Access denied: copying from blocked directory"}
        if not self._is_safe_path(destination):
            return {"error": "Access denied: copying to blocked directory"}
        src_blocked = self._gate("copy", source, mutate=False)
        if src_blocked:
            return src_blocked
        dst_blocked = self._gate("copy", destination, mutate=True)
        if dst_blocked:
            return dst_blocked
        try:
            src = Path(source)
            dst = Path(destination)
            if not src.exists():
                return {"error": f"Source not found: {source}"}
            if src.is_dir():
                shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
            return {"success": True, "source": str(src), "destination": str(dst)}
        except Exception as e:
            return {"error": str(e)}

    def move(self, source: str, destination: str) -> Dict:
        if not self._is_safe_path(source):
            return {"error": "Access denied: moving from blocked directory"}
        if not self._is_safe_path(destination):
            return {"error": "Access denied: moving to blocked directory"}
        src_blocked = self._gate("move", source, mutate=True)
        if src_blocked:
            return src_blocked
        dst_blocked = self._gate("move", destination, mutate=True)
        if dst_blocked:
            return dst_blocked
        try:
            src = Path(source)
            dst = Path(destination)
            if not src.exists():
                return {"error": f"Source not found: {source}"}
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return {"success": True, "source": str(src), "destination": str(dst)}
        except Exception as e:
            return {"error": str(e)}

    def delete(self, path: str) -> Dict:
        if not self._is_safe_path(path):
            return {"error": "Access denied: deleting in blocked directory"}
        blocked = self._gate("delete", path)
        if blocked:
            return blocked
        try:
            p = Path(path)
            if not p.exists():
                return {"error": f"Not found: {path}"}
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink()
            return {"success": True, "deleted": str(p)}
        except Exception as e:
            return {"error": str(e)}

    def info(self, path: str) -> Dict:
        try:
            gate = self._gate("info", path, mutate=False)
            if gate:
                return gate
            p = Path(path)
            if not p.exists():
                return {"error": f"Not found: {path}"}
            stat = p.stat()
            return {
                "path": str(p),
                "type": "dir" if p.is_dir() else "file",
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "created": stat.st_ctime,
                "extension": p.suffix,
                "exists": True,
            }
        except Exception as e:
            return {"error": str(e)}

    def read_pdf(self, path: str) -> Dict:
        from .pdf_reader import read_pdf

        return read_pdf(path)
