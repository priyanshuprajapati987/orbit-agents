# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Browser Tool — Playwright-based browser automation.

Navigate, click, type, screenshot, extract content from web pages.
Runs headless by default, headed for debugging.
"""

import base64
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Any

from ..logging_config import get_logger

logger = get_logger(__name__)

_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass


MAX_RETRIES = 3
RETRY_DELAY_MS = 500


class BrowserTool:
    def __init__(self, headless: bool = True):
        self.name = "browser"
        self.description = "Browser automation — navigate, click, type, extract"
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._last_error = None
        self._retry_count = 0

    def _gate(self, action: str):
        from ..core.policy_engine import check_tool

        decision = check_tool("browser", action)
        if not decision.allowed:
            return {
                "success": False,
                "error": f"Access denied ({decision.reason}): {action}",
                "policy": decision.to_dict(),
            }
        return None

    def _retry(self, func, *args, **kwargs):
        """Execute func with retry on transient failures."""
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                # Only retry on transient errors
                if any(kw in err_str for kw in ("timeout", "connection", "network", "disconnected", "crash")):
                    logger.warning("Browser retry %d/%d: %s", attempt + 1, MAX_RETRIES, e)
                    time.sleep(RETRY_DELAY_MS / 1000 * (attempt + 1))
                    # Reconnect if page is gone
                    if "disconnected" in err_str or "crash" in err_str:
                        self._page = None
                        self._context = None
                        try:
                            self._ensure()
                        except Exception:
                            logger.warning("Browser reconnect failed after %s", err_str)
                else:
                    raise
        raise last_error

    def _ensure(self):
        if not _PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed. `pip install playwright && playwright install chromium`")
        if not self._playwright:
            self._playwright = sync_playwright().start()
        if not self._browser:
            launched = False
            for attempt in range(2):
                try:
                    kwargs = {
                        "headless": self.headless,
                        "args": ["--dns-servers=8.8.8.8", "--disable-gpu", "--no-sandbox"],
                    }
                    if attempt == 1:
                        # Last resort: an explicitly configured system browser.
                        # Never a hardcoded path (breaks Linux/macOS/other
                        # install locations) and never force headed mode
                        # (crashes on headless servers/containers).
                        env_path = os.environ.get("ORBIT_CHROME_PATH", "")
                        which_path = shutil.which("chrome") or shutil.which("chromium") or shutil.which(
                            "msedge"
                        )
                        fallback = env_path or which_path or ""
                        if not fallback:
                            raise RuntimeError("no fallback browser found")
                        kwargs["executable_path"] = fallback
                    self._browser = self._playwright.chromium.launch(**kwargs)
                    launched = True
                    break
                except Exception:
                    if attempt == 1:
                        logger.warning("Browser launch failed on all attempts")
            if not launched:
                self._playwright.stop()
                self._playwright = None
                raise RuntimeError("Failed to launch browser. Run: playwright install chromium")
        try:
            if not self._context:
                self._context = self._browser.new_context(viewport={"width": 1280, "height": 720})
            if not self._page:
                self._page = self._context.new_page()
        except Exception:
            self.close()
            raise

    def is_available(self) -> bool:
        return _PLAYWRIGHT_AVAILABLE

    def navigate(self, url: str, timeout: int = 30000) -> Dict[str, Any]:
        try:
            # SSRF protection: block internal/private IPs
            from ..core.ssrf_guard import get_ssrf_guard

            guard = get_ssrf_guard()
            err = guard.validate_outbound(url)
            if err:
                return {"error": err}
            self._ensure()
            self._retry(self._page.goto, url, timeout=timeout, wait_until="domcontentloaded")
            self._last_error = None
            self._retry_count = 0
            return {
                "status": "ok",
                "url": self._page.url,
                "title": self._page.title(),
            }
        except Exception as e:
            self._last_error = str(e)
            self._retry_count += 1
            return {"error": str(e), "retry_count": self._retry_count}

    def get_content(self) -> Dict[str, Any]:
        try:
            self._ensure()
            title = self._page.title()
            text = self._page.inner_text("body")[:5000]
            links = [a.get_attribute("href") for a in self._page.query_selector_all("a[href]")[:50]]
            return {
                "title": title,
                "url": self._page.url,
                "text": text,
                "links": links[:20],
            }
        except Exception as e:
            return {"error": str(e)}

    def screenshot(self, full_page: bool = False) -> Dict[str, Any]:
        try:
            self._ensure()
            import tempfile

            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
            try:
                self._retry(self._page.screenshot, path=tmp, full_page=full_page)
                with open(tmp, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                return {"screenshot_base64": b64, "format": "png"}
            finally:
                Path(tmp).unlink(missing_ok=True)
        except Exception as e:
            return {"error": str(e)}

    def click(self, selector: str, timeout: int = 5000) -> Dict[str, Any]:
        try:
            g = self._gate("click")
            if g:
                return g
            self._ensure()
            self._retry(self._page.click, selector, timeout=timeout)
            return {"status": "ok", "clicked": selector, "url": self._page.url}
        except Exception as e:
            return {"error": str(e)}

    def type_text(self, selector: str, text: str, clear: bool = True) -> Dict[str, Any]:
        try:
            g = self._gate("type")
            if g:
                return g
            self._ensure()
            if clear:
                self._page.fill(selector, "")
            self._page.type(selector, text, delay=30)
            return {"status": "ok", "filled": selector}
        except Exception as e:
            return {"error": str(e)}

    def press_key(self, key: str) -> Dict[str, Any]:
        try:
            g = self._gate("press_key")
            if g:
                return g
            self._ensure()
            self._page.keyboard.press(key)
            return {"status": "ok", "key": key}
        except Exception as e:
            return {"error": str(e)}

    def wait_for(self, selector: str, timeout: int = 10000) -> Dict[str, Any]:
        """Wait for an element to appear on the page."""
        try:
            g = self._gate("wait_for")
            if g:
                return g
            self._ensure()
            self._page.wait_for_selector(selector, timeout=timeout)
            return {"status": "ok", "found": selector}
        except Exception as e:
            return {"error": str(e)}

    def scroll(self, direction: str = "down", amount: int = 3) -> Dict[str, Any]:
        """Scroll the page up or down."""
        try:
            g = self._gate("scroll")
            if g:
                return g
            self._ensure()
            pixels = amount * 500
            if direction == "down":
                self._page.mouse.wheel(0, pixels)
            else:
                self._page.mouse.wheel(0, -pixels)
            return {"status": "ok", "scrolled": direction, "amount": pixels}
        except Exception as e:
            return {"error": str(e)}

    def evaluate(self, script: str) -> Dict[str, Any]:
        g = self._gate("evaluate")
        if g:
            return g
        try:
            self._ensure()
            result = self._page.evaluate(script)
            return {"status": "ok", "result": str(result)[:2000]}
        except Exception as e:
            return {"error": str(e)}

    def search_google(self, query: str) -> Dict[str, Any]:
        try:
            self._ensure()
            self._page.goto(
                f"https://www.google.com/search?q={query.replace(' ', '+')}",
                timeout=15000,
                wait_until="domcontentloaded",
            )
            results = []
            items = self._page.query_selector_all("div.g")
            for item in items[:5]:
                title_el = item.query_selector("h3")
                link_el = item.query_selector("a")
                snippet_el = item.query_selector("div[data-sncf], span.aCOpRe")
                results.append(
                    {
                        "title": title_el.inner_text() if title_el else "",
                        "url": link_el.get_attribute("href") if link_el else "",
                        "snippet": snippet_el.inner_text() if snippet_el else "",
                    }
                )
            return {"query": query, "results": results, "count": len(results)}
        except Exception as e:
            return {"error": str(e)}

    def extract_page(self, url: str) -> Dict[str, Any]:
        nav = self.navigate(url)
        if "error" in nav:
            return nav
        content = self.get_content()
        ss = self.screenshot()
        content["screenshot"] = ss.get("screenshot_base64", "")[:100]
        return content

    def close(self):
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            logger.exception("Browser close failed")
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    def get_status(self) -> Dict[str, Any]:
        """Get current browser state."""
        return {
            "available": _PLAYWRIGHT_AVAILABLE,
            "running": self._browser is not None,
            "page_loaded": self._page is not None,
            "current_url": self._page.url if self._page else None,
            "last_error": self._last_error,
            "retry_count": self._retry_count,
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
