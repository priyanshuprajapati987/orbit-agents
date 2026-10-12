# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Code Completion Tool

Provides inline code suggestions, next-edit predictions, and code generation
similar to Cloud Code's code completion features.

Features:
- Context-aware code completion based on file content and cursor position
- Next-edit predictions (suggestions beyond cursor)
- Line completion from partial input
- Multi-language support (Python, JS/TS, Java, etc.)
"""

import os
import re
from typing import Dict, List, Optional

from ..logging_config import get_logger

logger = get_logger(__name__)


# ── Language Detection ─────────────────────────────────────────────────────

_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".md": "markdown",
}


def detect_language(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    return _LANG_MAP.get(ext, "text")


# ── Pattern-Based Completion (fast, no LLM) ────────────────────────────────

_PATTERNS = {
    "python": {
        r"^\s*def\s+\w+\s*\(.*\)\s*:$": "    pass\n",
        r"^\s*class\s+\w+.*:$": "    pass\n",
        r"^\s*if\s+.*:$": "    pass\n",
        r"^\s*for\s+\w+\s+in\s+.*:$": "    pass\n",
        r"^\s*while\s+.*:$": "    pass\n",
        r"^\s*try\s*:$": "    pass\nexcept Exception as e:\n    pass\n",
        r"^\s*with\s+.*:$": "    pass\n",
        r"^\s*async\s+def\s+\w+.*:$": "    pass\n",
        r"^\s*@\w+.*$": "",
        r"^\s*import\s+\w+.*$": "",
        r"^\s*from\s+\w+\s+import\s+.*$": "",
        r"^\s*print\(.*\)\s*$": "",
        r"^\s*return\s+.*$": "",
        r"^\s*__init__\.py\s*$": "",
    },
    "javascript": {
        r"^\s*function\s+\w+\s*\(.*\)\s*\{?\s*$": "  \n}\n",
        r"^\s*const\s+\w+\s*=\s*\(.*\)\s*=>\s*\{?\s*$": "  \n}\n",
        r"^\s*if\s*\(.*\)\s*\{?\s*$": "  \n}\n",
        r"^\s*for\s*\(.*\)\s*\{?\s*$": "  \n}\n",
        r"^\s*while\s*\(.*\)\s*\{?\s*$": "  \n}\n",
        r"^\s*try\s*\{?\s*$": "  \n} catch (e) {\n  \n}\n",
        r"^\s*class\s+\w+.*\{?\s*$": "  constructor() {\n  }\n}\n",
    },
    "typescript": {
        r"^\s*function\s+\w+\s*\(.*\)\s*:\s*\w+\s*\{?\s*$": "  \n}\n",
        r"^\s*const\s+\w+\s*:\s*\w+\s*=\s*\(.*\)\s*=>\s*\{?\s*$": "  \n}\n",
        r"^\s*interface\s+\w+.*\{?\s*$": "  \n}\n",
        r"^\s*type\s+\w+\s*=.*$": "",
        r"^\s*if\s*\(.*\)\s*\{?\s*$": "  \n}\n",
    },
}


def _get_pattern_suggestion(line: str, language: str) -> Optional[str]:
    patterns = _PATTERNS.get(language, {})
    for pattern, suggestion in patterns.items():
        if re.match(pattern, line):
            return suggestion if suggestion else None
    return None


# ── Import Suggestions ─────────────────────────────────────────────────────

_IMPORT_SUGGESTIONS = {
    "python": [
        "import os",
        "import sys",
        "import json",
        "from pathlib import Path",
        "from typing import List, Dict, Optional, Any",
        "import requests",
        "import numpy as np",
        "import pandas as pd",
        "from dataclasses import dataclass",
        "import logging",
        "from enum import Enum",
        "from abc import ABC, abstractmethod",
    ],
    "javascript": [
        "import React from 'react'",
        "import { useState, useEffect } from 'react'",
        "import express from 'express'",
        "const fs = require('fs')",
        "const path = require('path')",
        "import axios from 'axios'",
    ],
    "typescript": [
        "import React from 'react'",
        "import { useState, useEffect } from 'react'",
        "interface Props {}",
        "type State = {}",
        "import express from 'express'",
    ],
}


# ── Main Completion Engine ─────────────────────────────────────────────────


class CodeCompletionEngine:
    """Provides code completion suggestions for a given file context."""

    def __init__(self):
        self._analyzed: Dict[str, Dict] = {}
        self._analyzed_max = 1000

    def _analyze_file(self, file_path: str) -> Dict:
        """Analyze a file and cache imports, functions, classes."""
        if file_path in self._analyzed:
            return self._analyzed[file_path]

        # Evict oldest if at capacity
        if len(self._analyzed) >= self._analyzed_max:
            oldest_key = next(iter(self._analyzed))
            del self._analyzed[oldest_key]

        result = {
            "imports": [],
            "functions": [],
            "classes": [],
            "variables": [],
            "language": detect_language(file_path),
        }

        if not os.path.isfile(file_path):
            self._analyzed[file_path] = result
            return result

        try:
            with open(file_path) as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            self._analyzed[file_path] = result
            return result

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                result["imports"].append(stripped)
            elif stripped.startswith("def ") or stripped.startswith("async def "):
                m = re.match(r"(?:async\s+)?def\s+(\w+)\s*\(.*\)\s*(?:->.*)?:", stripped)
                if m:
                    result["functions"].append(m.group(1))
            elif stripped.startswith("class "):
                m = re.match(r"class\s+(\w+).*:", stripped)
                if m:
                    result["classes"].append(m.group(1))

        self._analyzed[file_path] = result
        return result

    def complete_line(
        self,
        file_path: str,
        line: str,
        line_number: int,
        context_before: List[str] = None,
        context_after: List[str] = None,
    ) -> Optional[str]:
        """Get a completion suggestion for the current line.

        Returns the suggestion text or None if no suggestion.
        """
        language = detect_language(file_path)
        context_before = context_before or []
        context_after = context_after or []

        lang_lower = language.lower()

        # 1. Pattern-based completion
        suggestion = _get_pattern_suggestion(line, lang_lower)
        if suggestion:
            return suggestion

        # 2. Import suggestion (only at file start)
        stripped = line.strip()
        if stripped == "import" or stripped == "from":
            suggestions = _IMPORT_SUGGESTIONS.get(lang_lower, [])
            if suggestions:
                return suggestions[0][len(stripped) :]

        # 3. Parent class constructor call
        if re.match(r"^\s+super\(\)\.__init__\(\)\s*$", line):
            return ""

        # 4. File-level: suggest __init__ or main guard
        if lang_lower == "python" and line.strip() == "":
            if any("if __name__" in l for l in context_before):
                return None
            if line_number > 10 and not context_after:
                return "if __name__ == '__main__':\n    main()\n"

        return None

    def suggest_completions(self, file_path: str, cursor_line: int, cursor_col: int, lines: List[str]) -> List[Dict]:
        """Get ranked completion suggestions for current cursor position.

        Returns list of dicts: {text, type, detail, score}
        """
        language = detect_language(file_path)
        analysis = self._analyze_file(file_path)
        current_line = lines[cursor_line - 1] if lines else ""
        prefix = current_line[:cursor_col]

        lang_lower = language.lower()
        completions = []

        # Imports matching
        if re.match(r"^\s*import\s+\w*$", prefix):
            word = prefix.strip().split()[-1] if prefix.strip().split() else ""
            for imp in analysis["imports"]:
                if word and word.lower() in imp.lower():
                    completions.append(
                        {
                            "text": imp[len(prefix.rstrip().rsplit(" ", 1)[0]) - len(prefix) :]
                            if " " in prefix.strip()
                            else imp,
                            "type": "import",
                            "detail": imp,
                            "score": 0.8,
                        }
                    )

        # Function names
        if current_line.strip().startswith(("def ", "async def ")):
            m = re.match(r"(?:async\s+)?def\s+(\w*)", current_line)
            if m:
                partial = m.group(1).lower()
                for fn in analysis["functions"]:
                    if partial and fn.lower().startswith(partial):
                        completions.append(
                            {
                                "text": fn[len(partial) :],
                                "type": "function",
                                "detail": f"def {fn}(...)",
                                "score": 0.7,
                            }
                        )
                for cls in analysis["classes"]:
                    if partial and cls.lower().startswith(partial):
                        completions.append(
                            {
                                "text": cls[len(partial) :],
                                "type": "class",
                                "detail": f"class {cls}",
                                "score": 0.6,
                            }
                        )

        # Variable names (last 100 lines)
        var_pattern = re.compile(r"^\s*(\w+)\s*=")
        for i in range(max(0, cursor_line - 100), cursor_line - 1):
            m = var_pattern.match(lines[i] if i < len(lines) else "")
            if m and not m.group(1).startswith("_"):
                var_name = m.group(1)
                if not prefix.strip() or var_name.lower().startswith(prefix.strip().lower().split()[-1]):
                    completions.append(
                        {
                            "text": var_name[len(prefix.strip().split()[-1]) :] if prefix.strip().split() else var_name,
                            "type": "variable",
                            "detail": var_name,
                            "score": 0.5,
                        }
                    )

        # Keywords
        keywords = {
            "python": [
                "def ",
                "class ",
                "import ",
                "from ",
                "return ",
                "if ",
                "elif ",
                "else:",
                "for ",
                "while ",
                "try:",
                "except ",
                "with ",
                "async ",
                "await ",
                "pass",
                "break",
                "continue",
                "raise ",
                "yield ",
                "lambda ",
                "True",
                "False",
                "None",
                "self",
                "cls",
                "__init__",
            ],
            "javascript": [
                "function ",
                "const ",
                "let ",
                "var ",
                "return ",
                "if (",
                "else ",
                "for (",
                "while (",
                "try {",
                "catch (",
                "async ",
                "await ",
                "import ",
                "export ",
                "class ",
                "new ",
                "typeof ",
                "throw ",
            ],
            "typescript": [
                "function ",
                "const ",
                "let ",
                "var ",
                "interface ",
                "type ",
                "return ",
                "if (",
                "else ",
                "for (",
                "while (",
                "async ",
                "await ",
                "import ",
                "export ",
                "class ",
                "new ",
            ],
        }
        kw_list = keywords.get(lang_lower, [])
        prefix_word = prefix.strip().split()[-1] if prefix.strip() else ""
        for kw in kw_list:
            if prefix_word and kw.lower().startswith(prefix_word.lower()):
                tail = kw[len(prefix_word) :]
                completions.append(
                    {
                        "text": tail,
                        "type": "keyword",
                        "detail": kw.strip(),
                        "score": 0.4,
                    }
                )

        # Deduplicate and sort by score
        seen = set()
        unique = []
        for c in sorted(completions, key=lambda x: -x["score"]):
            key = c["text"]
            if key not in seen:
                seen.add(key)
                unique.append(c)

        return unique[:10]

    def suggest_next_edits(self, file_path: str, lines: List[str]) -> List[Dict]:
        """Predict next likely edits (like Cloud Code's Next Edit Predictions).

        Returns list of {line, type, suggestion} dicts.
        """
        language = detect_language(file_path)
        lang_lower = language.lower()
        predictions = []

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Empty function body
            if lang_lower == "python" and re.match(r"def \w+\(.*\):\s*$", stripped):
                if i + 1 >= len(lines) or not lines[i + 1].strip():
                    predictions.append(
                        {
                            "line": i + 1,
                            "type": "fill_body",
                            "suggestion": "    pass\n",
                            "current": stripped,
                        }
                    )

            # Empty class body
            elif lang_lower == "python" and re.match(r"class \w+.*:\s*$", stripped):
                if i + 1 >= len(lines) or not lines[i + 1].strip():
                    predictions.append(
                        {
                            "line": i + 1,
                            "type": "fill_body",
                            "suggestion": "    pass\n",
                            "current": stripped,
                        }
                    )

            # Missing docstring (triple-quoted strings)
            elif lang_lower == "python" and re.match(r"def \w+\(.*\):", stripped):
                if i + 1 < len(lines) and not lines[i + 1].strip().startswith(('"""', "'''", "#")):
                    predictions.append(
                        {
                            "line": i + 1,
                            "type": "add_docstring",
                            "suggestion": '    """TODO: add docstring"""\n',
                            "current": stripped,
                        }
                    )

            # Missing __init__ in class
            elif lang_lower == "python" and re.match(r"class \w+.*:", stripped):
                has_init = any("def __init__" in l for l in lines[i : i + 20])
                if not has_init:
                    predictions.append(
                        {
                            "line": i + 1,
                            "type": "add_init",
                            "suggestion": "    def __init__(self):\n        pass\n",
                            "current": stripped,
                        }
                    )

            # Unmatched opening braces (JS/TS)
            elif lang_lower in ("javascript", "typescript"):
                open_braces = stripped.count("{") - stripped.count("}")
                if open_braces > 0 and i + 1 < len(lines) and not lines[i + 1].strip():
                    predictions.append(
                        {
                            "line": i + 1,
                            "type": "close_brace",
                            "suggestion": "}\n",
                            "current": stripped,
                        }
                    )

        return predictions

    def clear_cache(self, file_path: Optional[str] = None):
        if file_path:
            self._analyzed.pop(file_path, None)
        else:
            self._analyzed.clear()


# ── Singleton ──────────────────────────────────────────────────────────────

_engine: Optional[CodeCompletionEngine] = None
_engine_lock = __import__("threading").Lock()


def get_engine() -> CodeCompletionEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = CodeCompletionEngine()
    return _engine
