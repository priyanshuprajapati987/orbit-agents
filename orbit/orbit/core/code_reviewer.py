# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
from .runtime_paths import data_dir
import ast
import json
import re
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..logging_config import get_logger

logger = get_logger(__name__)

DATA_DIR = data_dir() / "reviews"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CODE_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_+\-]*)\n(.*?)```", re.DOTALL)

_EXT_BY_LANG = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "java": ".java",
    "go": ".go",
    "rust": ".rs",
    "c": ".c",
    "cpp": ".cpp",
    "c++": ".cpp",
    "html": ".html",
    "css": ".css",
    "sql": ".sql",
}


def extract_code_blocks(text: str) -> List[str]:
    """Return code found inside ```lang ... ``` fences. Returns [] when no fence
    is present (callers decide how to handle raw/non-fenced text)."""
    return [b.strip() for b in CODE_FENCE_RE.findall(text) if b.strip()]


def verify_python_syntax(code: str) -> Tuple[bool, str]:
    """Return (ok, error_message) for a Python code snippet."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"{e.msg} (line {e.lineno})"
    except ValueError as e:
        return False, str(e)


SECRET_PATTERNS = [
    (r'password\s*=\s*["\'][^"\']+["\']', "Hardcoded password", "Use env var or secrets manager"),
    (r'secret\s*=\s*["\'][^"\']+["\']', "Hardcoded secret", "Move to .env / environment variable"),
    (r'api_key\s*=\s*["\'][^"\']+["\']', "Hardcoded API key", "Use environment variable"),
    (r'token\s*=\s*["\'][^"\']+["\']', "Hardcoded token", "Use environment variable"),
    (r'key\s*=\s*["\'][^"\']+["\']', "Possible hardcoded key", "Verify and move to env"),
]

SQL_INJECTION_PATTERNS = [
    (r'f["\'].*\{.*\}.*["\']\s*\.?\s*execute\b', "f-string in SQL execute", "Use parameterized queries"),
    (r'execute\(.*["\'].*%.*["\']', "String formatting in SQL", "Use parameterized queries"),
    (r"raw_sql\s*=", "Raw SQL assignment", "Use ORM / query builder"),
]

SAFETY_PATTERNS = [
    (r"\beval\s*\(", "Use of eval()", "Avoid eval — security risk"),
    (r"\bexec\s*\(", "Use of exec()", "Avoid exec — security risk"),
    (r"\b__import__\s*\(", "Use of __import__()", "Use regular imports"),
    (r"\bpickle\.loads?\s*\(", "Unsafe deserialization", "Use safe alternatives like JSON"),
]

STYLE_PATTERNS = [
    (r"^\s+class\s+\w+\(\)\s*:\s*$", "Empty class definition", "Add class docstring or pass"),
]

BUG_PATTERNS = [
    (r"^except\s*:", "Bare except clause", "Specify exception type"),
    (r"def\s+\w+\(.*=\s*\[\].*\)", "Mutable default argument (list)", "Use None and create list inside"),
    (r"def\s+\w+\(.*=\s*\{\}.*\)", "Mutable default argument (dict)", "Use None and create dict inside"),
    (r"^\s+pass\s*$", "Stub code (pass)", "Implement or raise NotImplementedError"),
]


@dataclass
class ReviewIssue:
    file: str
    line: int
    severity: str
    category: str
    message: str
    suggestion: str
    code_snippet: str = ""


@dataclass
class ReviewReport:
    project_name: str = ""
    files_reviewed: int = 0
    total_issues: int = 0
    issues: List[ReviewIssue] = field(default_factory=list)
    summary: Dict = field(default_factory=dict)
    reviewed_at: str = field(default_factory=lambda: datetime.now().isoformat())


def _check_line(
    line: str, lineno: int, filepath: str, patterns: List[Tuple], category: str, severity: str
) -> List[ReviewIssue]:
    issues = []
    for pattern, message, suggestion in patterns:
        if re.search(pattern, line):
            issues.append(
                ReviewIssue(
                    file=filepath,
                    line=lineno,
                    severity=severity,
                    category=category,
                    message=message,
                    suggestion=suggestion,
                    code_snippet=line.strip()[:120],
                )
            )
    return issues


def _classify_severity(category: str) -> str:
    if category == "security":
        return "critical"
    elif category == "bug":
        return "high"
    elif category == "safety":
        return "high"
    elif category in ("style", "quality"):
        return "low"
    return "medium"


class CodeReviewer:
    def __init__(self):
        self._lock = threading.Lock()
        self.reports: Dict[str, ReviewReport] = {}

    def review(self, generated_files: List[Dict], project_name: str = "unnamed") -> Dict:
        all_patterns = [
            ("security", "critical", SECRET_PATTERNS),
            ("security", "critical", SQL_INJECTION_PATTERNS),
            ("safety", "high", SAFETY_PATTERNS),
            ("bug", "high", BUG_PATTERNS),
            ("style", "low", STYLE_PATTERNS),
        ]

        all_issues = []
        file_count = 0

        for gf in generated_files:
            filepath = gf.get("path", "unknown")
            content = gf.get("content", "")
            lines = content.split("\n")
            file_count += 1

            # Line-by-line checks
            for lineno, line in enumerate(lines, 1):
                for category, severity, patterns in all_patterns:
                    issues = _check_line(line, lineno, filepath, patterns, category, severity)
                    all_issues.extend(issues)

            # File-level checks
            if len(lines) > 500:
                all_issues.append(
                    ReviewIssue(
                        file=filepath,
                        line=1,
                        severity="low",
                        category="quality",
                        message="File exceeds 500 lines",
                        suggestion="Consider splitting into smaller modules",
                    )
                )

            if "TODO" in content:
                all_issues.append(
                    ReviewIssue(
                        file=filepath,
                        line=content.find("TODO") + 1,
                        severity="low",
                        category="quality",
                        message="Contains TODO marker",
                        suggestion="Complete unfinished implementation before production",
                    )
                )

        by_category = {}
        by_severity = {}
        for issue in all_issues:
            by_category[issue.category] = by_category.get(issue.category, 0) + 1
            by_severity[issue.severity] = by_severity.get(issue.severity, 0) + 1

        report = ReviewReport(
            project_name=project_name,
            files_reviewed=file_count,
            total_issues=len(all_issues),
            issues=all_issues,
            summary={
                "by_category": by_category,
                "by_severity": by_severity,
                "pass": len(all_issues) == 0,
                "score": max(0, 100 - len(all_issues) * 5),
            },
        )

        with self._lock:
            self.reports[project_name] = report

        return self._report_to_dict(report)

    def review_code(self, content: str, language: str = "python") -> Dict:
        """Convenience: review a single inline code snippet (string) rather than
        a list of generated-file dicts. Returns the same report dict as review()."""
        ext = _EXT_BY_LANG.get(str(language).lower(), ".txt")
        return self.review(
            [{"path": f"snippet{ext}", "content": content}],
            project_name="inline",
        )

    def _report_to_dict(self, report: ReviewReport) -> Dict:
        return {
            "project_name": report.project_name,
            "files_reviewed": report.files_reviewed,
            "total_issues": report.total_issues,
            "issues": [
                {
                    "file": i.file,
                    "line": i.line,
                    "severity": i.severity,
                    "category": i.category,
                    "message": i.message,
                    "suggestion": i.suggestion,
                    "code_snippet": i.code_snippet,
                }
                for i in report.issues
            ],
            "summary": report.summary,
            "reviewed_at": report.reviewed_at,
        }

    def get_report(self, project_name: str) -> Optional[Dict]:
        with self._lock:
            report = self.reports.get(project_name)
            return self._report_to_dict(report) if report else None

    def list_reports(self) -> List[str]:
        with self._lock:
            return list(self.reports.keys())

    def save(self, filepath: Optional[str] = None):
        from ._security_utils import confine_result_path

        if filepath:
            path = confine_result_path(filepath, DATA_DIR / "review_reports.json")
            if path is None:
                raise ValueError(f"refusing to write outside allowed dirs: {filepath}")
        else:
            path = DATA_DIR / "review_reports.json"
        with self._lock:
            data = {name: asdict(r) for name, r in self.reports.items()}
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def load(self, filepath: Optional[str] = None):
        from ._security_utils import confine_result_path

        if filepath:
            path = confine_result_path(filepath, DATA_DIR / "review_reports.json")
            if path is None:
                logger.warning("refusing to read outside allowed dirs: %s", filepath)
                return
        else:
            path = DATA_DIR / "review_reports.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                with self._lock:
                    for name, rd in data.items():
                        issues = [ReviewIssue(**i) for i in rd.get("issues", [])]
                        rd["issues"] = issues
                        self.reports[name] = ReviewReport(**rd)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load reviews: %s", e)

    def reset(self):
        with self._lock:
            self.reports.clear()


_code_reviewer = None
_cr_lock = threading.Lock()


def get_code_reviewer() -> CodeReviewer:
    global _code_reviewer
    if _code_reviewer is None:
        with _cr_lock:
            if _code_reviewer is None:
                _code_reviewer = CodeReviewer()
    return _code_reviewer
