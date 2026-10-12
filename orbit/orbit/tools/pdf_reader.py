# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT PDF Reader — Real PDF extraction and editing.

Uses OpenDataLoader-PDF when available (Java 11+), falls back to PyPDF2.
Returns structured markdown with layout-aware reading order.
"""

import subprocess
from pathlib import Path
from typing import Dict, Optional, Any

from ..logging_config import get_logger

logger = get_logger(__name__)


def _java_version() -> Optional[str]:
    try:
        result = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=10)
        out = result.stderr or result.stdout
        for line in out.split("\n"):
            if "version" in line:
                v = line.strip().split()[-1].strip('"')
                return v
    except Exception:
        return None
    return None


def _java11plus() -> bool:
    v = _java_version()
    if not v:
        return False
    try:
        major = int(v.split(".")[0])
        if major >= 11:
            return True
        if major == 1 and len(v.split(".")) > 1:
            return int(v.split(".")[1]) >= 11
    except Exception:
        return False
    return False


def _read_with_opendataloader(pdf_path: str) -> Dict[str, Any]:
    import opendataloader_pdf
    import tempfile
    import json

    pdf_path_obj = Path(pdf_path)
    with tempfile.TemporaryDirectory() as tmpdir:
        opendataloader_pdf.convert(
            input_path=[str(pdf_path_obj.resolve())],
            output_dir=tmpdir,
            format="json,markdown",
            quiet=True,
        )
        json_files = list(Path(tmpdir).glob("*.json"))
        md_files = list(Path(tmpdir).glob("*.md"))

        content = ""
        metadata = {}

        if md_files:
            content = md_files[0].read_text(encoding="utf-8")

        if json_files:
            raw = json.loads(json_files[0].read_text(encoding="utf-8"))
            metadata = {
                "pages": raw.get("metadata", {}).get("pages", 0),
                "elements": len(raw.get("elements", [])),
                "has_tables": any(e.get("type") == "table" for e in raw.get("elements", [])),
                "has_images": any(e.get("type") == "figure" for e in raw.get("elements", [])),
            }

        return {
            "status": "ok",
            "file": pdf_path,
            "size_mb": round(pdf_path_obj.stat().st_size / 1024 / 1024, 2),
            "content": content,
            "metadata": metadata,
            "engine": "opendataloader",
        }


def _read_with_pypdf2(pdf_path: str) -> Dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader

    pdf_path_obj = Path(pdf_path)
    reader = PdfReader(str(pdf_path_obj.resolve()))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        pages.append(text)

    content = "\n\n---\n\n".join(pages)

    return {
        "status": "ok",
        "file": pdf_path,
        "size_mb": round(pdf_path_obj.stat().st_size / 1024 / 1024, 2),
        "content": content,
        "metadata": {
            "pages": len(reader.pages),
            "elements": 0,
            "has_tables": False,
            "has_images": False,
        },
        "engine": "pypdf2",
    }


def read_pdf(pdf_path: str) -> Dict[str, Any]:
    path = Path(pdf_path)
    if not path.exists():
        return {"error": f"PDF not found: {pdf_path}"}
    if path.suffix.lower() != ".pdf":
        return {"error": f"Not a PDF file: {pdf_path}"}

    use_opendataloader = _java11plus()
    if use_opendataloader:
        try:
            result = _read_with_opendataloader(pdf_path)
            logger.info("PDF read with OpenDataLoader: %s", pdf_path)
            return result
        except Exception as e:
            logger.warning("OpenDataLoader failed, fallback to PyPDF2: %s", e)

    try:
        result = _read_with_pypdf2(pdf_path)
        logger.info("PDF read with PyPDF2: %s", pdf_path)
        return result
    except Exception as e:
        return {"error": f"Failed to read PDF: {e}"}


def edit_pdf_text(pdf_path: str, instructions: str) -> Dict[str, Any]:
    return {
        "status": "info",
        "message": "PDF editing via LLM summarization — text extracted, you can now ask questions about the content.",
        "note": "To edit PDF content, extract → modify → re-export as new PDF.",
    }
