# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Version System — Semantic versioning with changelog

Tracks MAJOR.MINOR.PATCH, git hash, build time.
Persistent changelog for release history.
"""

from .runtime_paths import app_root, data_dir
import json
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..logging_config import get_logger

logger = get_logger(__name__)

DATA_DIR = data_dir() / "version"

try:
    from orbit.orbit import __version__ as _PKG_VERSION
except Exception:
    _PKG_VERSION = "3.7.0"


def _parse_version(value: str):
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return "0.0.0", 0, 0, 0
    major, minor, patch = (int(g) for g in match.groups())
    return value, major, minor, patch


VERSION, MAJOR, MINOR, PATCH = _parse_version(_PKG_VERSION)

_BUILD_STAMP_PATH = Path(__file__).resolve().parent.parent / "_build_stamp.txt"


def get_build_stamp() -> Dict[str, str]:
    """Release watermark written by scripts/stamp_build.py (version/commit/dirty/built).

    Empty dict when absent (dev checkouts). Lets any shipped build be traced
    back to the exact git commit it was cut from.
    """
    try:
        if not _BUILD_STAMP_PATH.is_file():
            return {}
        data: Dict[str, str] = {}
        for line in _BUILD_STAMP_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip()
        return data
    except Exception:
        return {}


class VersionSystem:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._history: List[Dict] = []
        self._load()

    def _path(self) -> Path:
        return DATA_DIR / "version_history.json"

    def _load(self):
        p = self._path()
        if p.exists():
            try:
                self._history = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                self._history = []

    def _save(self):
        from .runtime_paths import atomic_write_text

        p = self._path()
        atomic_write_text(p, json.dumps(self._history[-200:], indent=2, ensure_ascii=False))

    def _get_git_hash(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(app_root()),
                timeout=5,
            )
            return result.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    def get_version(self) -> Dict[str, Any]:
        # Subprocess OUTSIDE the lock: git rev-parse (5 s timeout) ran
        # under RLock on every /api/version call, stalling all readers.
        git_hash = self._get_git_hash()
        with self._lock:
            return {
                "version": VERSION,
                "major": MAJOR,
                "minor": MINOR,
                "patch": PATCH,
                "git_hash": git_hash,
                "build_time": datetime.now().isoformat(),
            }

    def increment_version(self, bump_type: str = "patch") -> Dict[str, Any]:
        global VERSION, MAJOR, MINOR, PATCH

        git_hash = self._get_git_hash()
        with self._lock:
            if bump_type == "major":
                MAJOR += 1
                MINOR = 0
                PATCH = 0
            elif bump_type == "minor":
                MINOR += 1
                PATCH = 0
            elif bump_type == "patch":
                PATCH += 1
            else:
                logger.warning("Unknown bump type: %s", bump_type)
                return self.get_version()

            VERSION = f"{MAJOR}.{MINOR}.{PATCH}"

            entry = {
                "version": VERSION,
                "bump_type": bump_type,
                "timestamp": datetime.now().isoformat(),
                "git_hash": git_hash,
            }
            self._history.append(entry)
            self._save()
            logger.info("Version bumped to %s (%s)", VERSION, bump_type)

        return self.get_version()

    def get_changelog(self) -> List[Dict]:
        return list(self._history)


_instance: Optional[VersionSystem] = None


def get_version_system() -> VersionSystem:
    global _instance
    if _instance is None:
        _instance = VersionSystem()
    return _instance
