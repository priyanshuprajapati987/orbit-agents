# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Runtime Paths — frozen-aware path resolver.

Single source of truth for locating application paths in both dev and
packaged (PyInstaller onedir) modes.

Frozen (onedir):
    app_root()      -> dirname(sys.executable)            (writable data next to .exe)
    bundle_root()   -> sys._MEIPASS (orbit/_internal)    (read-only bundled assets)
Dev (source tree):
    app_root()      -> repo root
    bundle_root()   -> repo root

Usage:
    from .runtime_paths import app_root, orbit_data_dir, frontend_dist
    data = orbit_data_dir() / "transcripts"
    static_dir = frontend_dist()
"""

import os
import sys
import tempfile
from pathlib import Path

_THIS = Path(__file__).resolve()
# runtime_paths.py lives at orbit/orbit/core/runtime_paths.py -> repo root = parents[3]
_REPO_ROOT = _THIS.parents[3]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Root for writable runtime data. Next to the .exe when frozen, else repo root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def bundle_root() -> Path:
    """Root for read-only bundled assets (frontend dist). _MEIPASS when frozen."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass).resolve() if meipass else app_root()
    return _REPO_ROOT


def repo_root() -> Path:
    """The source tree root (dev only). Same as app_root() in dev."""
    return _REPO_ROOT


def orbit_data_dir() -> Path:
    """Directory for runtime data (orbit_data/)."""
    return app_root() / "orbit_data"


def data_dir() -> Path:
    """Legacy data/ directory (memory, startup reports)."""
    return app_root() / "data"


def frontend_dist() -> Path:
    """Bundled React frontend build (ui_provided/dist)."""
    return bundle_root() / "ui_provided" / "dist"


def env_path() -> Path:
    """Canonical .env location. Next to the .exe when frozen."""
    return app_root() / ".env"


def is_env_bundled() -> bool:
    """True when frozen and the .env lives next to the executable."""
    return is_frozen() and env_path().exists()


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    """Write text atomically (tmp file in same dir + os.replace).

    Direct write_text truncates-then-fills: readers see torn content and
    a crash mid-write leaves a half file that loaders then treat as
    empty state (silent data loss on next save).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise


def atomic_write_json(path: Path | str, obj, indent: int = 2) -> None:
    """Atomic JSON write. See atomic_write_text."""
    import json as _json

    atomic_write_text(path, _json.dumps(obj, indent=indent, ensure_ascii=False, default=str))
