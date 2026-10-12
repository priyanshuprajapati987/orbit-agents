# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import base64
import logging
import os
import re
import tempfile
from pathlib import Path

import requests

from .config import OLLAMA_URL

logger = logging.getLogger(__name__)

VISION_MODELS = ["llava", "gemma3:4b", "gemma3:12b", "llava:7b", "llava:13b", "bakllava", "moondream", "minicpm-v"]

SCREEN_ANALYSIS_PROMPTS = {
    "quick": "Describe what is on this screen in 2-3 sentences. Focus on the main content and any visible text.",
    "detailed": "Describe this screen in detail. Include: 1) What application/window is open, 2) All visible text and UI elements (buttons, menus, tabs), 3) Layout and colors, 4) Any charts, images, or code visible.",
    "ui_elements": "List all UI elements visible on this screen: buttons, menus, text fields, checkboxes, tabs, links, icons. For each element, note its approximate position (top-left, center, bottom-right, etc.).",
    "code": "This appears to be a code editor. Describe: 1) What language/file is open, 2) Key functions or classes visible, 3) Any errors or warnings shown, 4) The general structure of the code.",
    "browser": "This appears to be a web browser. Describe: 1) The URL or website name, 2) Main content of the page, 3) Any forms, buttons, or interactive elements, 4) Sidebar or navigation elements.",
    "terminal": "This appears to be a terminal/command prompt. Describe: 1) What commands have been run recently, 2) Any output or errors, 3) Current directory and prompt.",
    "document": "This appears to be a document or text editor. Describe: 1) The document title or topic, 2) Main headings or sections, 3) Key content visible, 4) Formatting (bold, lists, tables).",
    "ocr": "Read ALL text visible on this screen exactly as it appears. Preserve formatting, line breaks, and structure. Include text in menus, buttons, dialogs, and main content areas.",
}


def _detect_vision_model() -> str:
    """Detect available vision model from Ollama."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            for vm in VISION_MODELS:
                for installed in models:
                    if vm in installed:
                        return installed
    except Exception:
        pass
    return "llava"


_VISION_CACHE = {}
_VISION_CACHE_MAX = 64


def vision_analyze(
    image_b64: str, prompt: str, mime: str = "image/png", vision_model: str = None, timeout: int = 120
) -> str:
    """Analyze a raw-base64 image with a vision model.

    Fallback chain: local Ollama vision model -> OpenRouter gpt-4o-mini ->
    empty string. Returns the model's text response, or "" if every vision
    path is unavailable (callers provide their own graceful fallback).

    Results are cached by (prompt, image hash) so identical frames (e.g. an
    idle desktop captured repeatedly) are not re-analyzed on every tick.
    """
    if not image_b64:
        return ""

    cache_key = (prompt, hash(image_b64))
    if cache_key in _VISION_CACHE:
        return _VISION_CACHE[cache_key]

    if vision_model is None:
        vision_model = _detect_vision_model()

    result = ""
    # 1) Local Ollama vision model (images array is the supported channel).
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": f"[MULTI-MODAL ANALYSIS]\n{prompt}",
                        "images": [image_b64],
                    }
                ],
                "stream": False,
                "options": {"temperature": 0.4},
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            result = resp.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        logger.warning("Ollama vision (%s) failed: %s", vision_model, e)

    # 2) OpenRouter vision fallback (gpt-4o-mini accepts image_url parts).
    if not result:
        try:
            from .config import decrypt_env

            key = decrypt_env("OPENROUTER_API_KEY", "")
            if key and key != "your_openrouter_api_key_here":
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "openai/gpt-4o-mini",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                                ],
                            }
                        ],
                    },
                    timeout=30,
                )
                if resp.status_code == 200:
                    result = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("OpenRouter vision failed: %s", e)

    if len(_VISION_CACHE) >= _VISION_CACHE_MAX:
        _VISION_CACHE.clear()
    _VISION_CACHE[cache_key] = result
    return result


def capture_screen():
    tmp = None
    try:
        import mss

        with mss.mss() as sct:
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            mss.tools.to_png(screenshot.rgb, screenshot.size, output=tmp_path)
            return tmp_path
    except Exception as e:
        logger.error("Screenshot capture failed (mss): %s", e)
        if tmp:
            try:
                os.unlink(tmp_path)
            except Exception:
                logger.exception("Failed to clean up temp mss screenshot")

    tmp2 = None
    try:
        import pyautogui

        tmp2 = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp2_name = tmp2.name
        tmp2.close()
        pyautogui.screenshot(tmp2_name)
        return tmp2_name
    except Exception as e:
        logger.error("Screenshot capture failed (pyautogui): %s", e)
        if tmp2:
            try:
                os.unlink(tmp2_name)
            except Exception:
                logger.exception("Failed to clean up temp pyautogui screenshot")
        return None


def analyze_screenshot(image_path, prompt="What do you see on this screen? Describe it briefly.", mode="quick"):
    """Analyze a screenshot with content-aware prompting.

    Modes: quick, detailed, ui_elements, code, browser, terminal, document, ocr
    """
    if not image_path or not os.path.exists(image_path):
        return {"text": "Screenshot capture failed.", "image_b64": None}

    img_b64 = _image_to_base64(image_path)
    raw_b64 = None
    try:
        with open(image_path, "rb") as f:
            raw_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        pass

    # Use mode-specific prompt if available, otherwise use provided prompt
    effective_prompt = SCREEN_ANALYSIS_PROMPTS.get(mode, prompt)

    # Auto-detect content type if mode is "quick" and prompt is default
    if mode == "quick" and "What do you see" in prompt:
        try:
            from PIL import Image

            img = Image.open(image_path)
            w, h = img.size
            # Very small image = likely icon, use detailed
            if w < 200 or h < 200:
                effective_prompt = SCREEN_ANALYSIS_PROMPTS["detailed"]
        except Exception:
            pass

    prompt_lower = effective_prompt.lower()
    # Word-boundary match: bare substrings misfire on ordinary English
    # ("read" in "already", "says" in prompts, "text" in "text layout").
    ocr_triggers = ("ocr", "text", "read", "say", "extract", "written", "says", "visible")
    is_ocr_request = mode == "ocr" or any(
        re.search(r"\b" + re.escape(t) + r"\b", prompt_lower) for t in ocr_triggers
    )

    if is_ocr_request:
        try:
            import pytesseract
            from PIL import Image

            text = pytesseract.image_to_string(Image.open(image_path))
            if text.strip():
                return {"text": f"**Text from screen:**\n```\n{text.strip()}\n```", "image_b64": img_b64}
        except Exception as e:
            logger.warning("OCR failed: %s", e)

    if raw_b64:
        vision_text = vision_analyze(raw_b64, effective_prompt, "image/png")
        if vision_text:
            return {"text": vision_text, "image_b64": img_b64}

    # Graceful fallback when no vision backend is available.
    try:
        from PIL import Image

        img = Image.open(image_path)
        w, h = img.size
        avg_color = tuple(int(x) for x in img.resize((1, 1)).getpixel((0, 0)))
        return {
            "text": f"Screenshot captured ({w}x{h}, avg color: RGB{avg_color})\nVision model unavailable.",
            "image_b64": img_b64,
        }
    except Exception:
        return {"text": "Screenshot captured but analysis unavailable.", "image_b64": img_b64}


def capture_and_analyze(prompt="What do you see on this screen?", mode="quick"):
    """Capture screenshot + analyze in one call. Returns {text, image_b64, path}."""
    path = capture_screen()
    if not path:
        return {"text": "Screenshot capture failed.", "image_b64": None, "path": None}
    result = analyze_screenshot(path, prompt, mode=mode)
    result["path"] = path
    return result


def _image_to_base64(image_path: str) -> str:
    """Convert image file to base64 data URI."""
    try:
        with open(image_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        ext = Path(image_path).suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "image/png")
        return f"data:{mime};base64,{data}"
    except Exception:
        return ""


def cleanup(path):
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except Exception:
            logger.exception("Failed to cleanup screenshot file")
