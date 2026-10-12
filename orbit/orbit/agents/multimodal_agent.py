# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Multi-Modal Agent — Vision, Screenshot, PDF, OCR

Supports image analysis, screenshot understanding, chart reading,
PDF summarization, and image-to-text via Gemini Vision.
"""

import base64
from pathlib import Path
from typing import Dict, Optional, Any
from dataclasses import dataclass, field

from ..logging_config import get_logger
from ..config import TITLE
from .base import BaseAgent

logger = get_logger(__name__)


@dataclass
class AnalysisResult:
    source_type: str = ""
    source_path: str = ""
    content: str = ""
    metadata: Dict = field(default_factory=dict)
    timestamp: str = ""


class MultiModalAgent(BaseAgent):
    label = "MMA"
    name = "MultiModal"
    color = "#a855f7"
    DEFAULT_PROMPT = (
        "You are a multi-modal analysis agent. Analyze images, screenshots, PDFs, and charts with precision."
    )

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._task_type = "vision"

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "image",
            "screenshot",
            "picture",
            "photo",
            "pdf",
            "chart",
            "graph",
            "analyze image",
            "read image",
            "ocr",
        ]
        return any(kw in text.lower() for kw in keywords)

    def _run(self, text, context=None):
        text_lower = text.lower()

        # OCR request - use OCR mode
        if any(kw in text_lower for kw in ("ocr", "read text", "extract text", "what does it say", "what's written")):
            import re

            match = re.search(r"[\w\/\\:.\~]+\.(png|jpg|jpeg)", text)
            if match:
                result = self.ocr_image(match.group(0))
                return result.get("text", str(result))
            # No file specified - capture and OCR
            return self._capture_and_ocr()

        # Screenshot analysis - detect content type
        if any(kw in text_lower for kw in ("screenshot", "screen", "what's on screen", "what do you see")):
            mode = self._detect_content_mode(text_lower)
            result = self.analyze_screenshot(mode=mode)
            return result.get("analysis", result.get("message", str(result)))

        # Image file analysis
        image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
        for ext in image_exts:
            if ext in text_lower:
                import re

                match = re.search(r"[\w\/\\:.\~]+\." + ext[1:], text)
                if match:
                    return self.analyze_image(match.group(0))

        # Generic analysis
        return self.analyze_image_text(text)

    def _detect_content_mode(self, text_lower):
        """Detect what type of content is likely on screen."""
        if any(kw in text_lower for kw in ("code", "editor", "ide", "vscode", "pycharm")):
            return "code"
        if any(kw in text_lower for kw in ("browser", "website", "chrome", "firefox", "edge", "page")):
            return "browser"
        if any(kw in text_lower for kw in ("terminal", "cmd", "powershell", "shell", "command")):
            return "terminal"
        if any(kw in text_lower for kw in ("document", "word", "notepad", "text file", "doc")):
            return "document"
        if any(kw in text_lower for kw in ("button", "menu", "ui", "element", "click")):
            return "ui_elements"
        if any(kw in text_lower for kw in ("detail", "describe", "everything", "all")):
            return "detailed"
        return "quick"

    def _capture_and_ocr(self):
        """Capture screenshot and OCR it."""
        try:
            from ..screen import capture_screen

            path = capture_screen()
            if path:
                return self.ocr_image(path)
        except Exception as e:
            return {"text": f"Screen capture failed: {e}"}
        return {"text": "Screen capture unavailable."}

    def analyze_image(self, image_path: str) -> Dict[str, Any]:
        path = Path(image_path)
        if not path.exists():
            return {"error": f"File not found: {image_path}"}

        import time as _time

        start = _time.time()
        try:
            img_data = base64.b64encode(path.read_bytes()).decode("utf-8")
            ext = path.suffix.lower()
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(ext, "image/png")

            prompt = "Describe this image in detail. What do you see? Include text, objects, colors, layout, and any charts or graphs."

            from ..screen import _detect_vision_model, vision_analyze

            vision_model = _detect_vision_model()
            response = vision_analyze(img_data, prompt, mime, vision_model)

            latency_ms = int((_time.time() - start) * 1000)
            return {
                "status": "analyzed",
                "file": str(path),
                "size_kb": round(path.stat().st_size / 1024, 1),
                "type": mime,
                "analysis": response or "Image analyzed — vision pipeline active (no LLM response)",
                "vision_model": vision_model,
                "latency_ms": latency_ms,
            }
        except Exception as e:
            return {"error": str(e)}

    def analyze_image_text(self, text: str) -> Dict[str, Any]:
        # Real behavior (not a canned stub): try to find an image path in the
        # text, otherwise analyze the live screen. Only return an honest error if
        # neither is possible — never a fake "pipeline ready" success string.
        import re

        image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
        for ext in image_exts:
            match = re.search(r"[\w\/\\:.]+" + re.escape(ext), text)
            if match:
                return self.analyze_image(match.group(0))
        try:
            from ..screen import capture_and_analyze

            result = capture_and_analyze("Describe what you see in this screenshot in detail.")
            analysis = result.get("text")
            if analysis:
                return {
                    "status": "analyzed",
                    "analysis": analysis,
                    "image_b64": result.get("image_b64"),
                    "path": result.get("path"),
                }
        except Exception as e:
            return {"status": "error", "error": f"No image path found in request and live screen capture failed: {e}"}
        return {"status": "error", "error": "No image path found and screen capture unavailable."}

    def analyze_screenshot(self, mode="quick") -> Dict[str, Any]:
        try:
            from ..screen import capture_and_analyze

            result = capture_and_analyze(
                "Describe this screenshot in detail. Include text, UI elements, layout.",
                mode=mode,
            )
            return {
                "status": "analyzed",
                "analysis": result.get("text", "Screenshot analyzed"),
                "image_b64": result.get("image_b64"),
                "path": result.get("path"),
                "mode": mode,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def analyze_pdf(self, pdf_path: str) -> Dict[str, Any]:
        from ..tools.pdf_reader import read_pdf

        result = read_pdf(pdf_path)
        if "error" in result:
            return result

        content = result.get("content", "")
        meta = result.get("metadata", {})
        engine = result.get("engine", "unknown")

        response = ""
        if content:
            from ..core.llm import legacy_adapter as llm_router

            try:
                response = llm_router.generate(
                    prompt=f"Summarize this PDF content and extract key insights:\n\n{content[:4000]}",
                    system_prompt="You are ORBIT PDF analyst. Summarize accurately with key points.",
                )
            except Exception:
                response = f"Content extracted ({len(content)} chars). Ask me anything about it, {TITLE}."

        return {
            "status": "analyzed",
            "file": result["file"],
            "size_mb": result["size_mb"],
            "pages": meta.get("pages", 0),
            "engine": engine,
            "content_preview": content[:2000],
            "analysis": response or content[:1000],
        }

    def analyze_chart(self, image_path: str) -> Dict[str, Any]:
        result = self.analyze_image(image_path)
        result["chart_analysis"] = True
        return result

    def ocr_image(self, image_path: str) -> Dict[str, Any]:
        path = Path(image_path)
        if not path.exists():
            return {"error": f"File not found: {image_path}"}
        try:
            import pytesseract
            from PIL import Image

            text = pytesseract.image_to_string(Image.open(image_path))
            return {
                "status": "success",
                "file": str(path),
                "text": text.strip() if text.strip() else "No text detected in image.",
            }
        except ImportError:
            try:
                from ..screen import analyze_screenshot

                result = analyze_screenshot(image_path, "Extract all text from this image using OCR.", mode="ocr")
                return {
                    "status": "success",
                    "file": str(path),
                    "text": result.get("text", "") if isinstance(result, dict) else str(result),
                }
            except Exception as e:
                return {"status": "error", "file": str(path), "error": str(e)}
        except Exception as e:
            return {"status": "error", "file": str(path), "error": str(e)}


_instance: Optional[MultiModalAgent] = None


def get_multimodal_agent() -> MultiModalAgent:
    global _instance
    if _instance is None:
        _instance = MultiModalAgent()
    return _instance
