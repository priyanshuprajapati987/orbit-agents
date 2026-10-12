# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
SEOTool - Real SEO analysis using page fetching and HTML parsing.

Provides on-page SEO auditing, meta tag validation, heading hierarchy analysis,
image audit, technical SEO checks, and keyword density calculation.
"""

import re
import logging
from urllib.parse import urlparse
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


class SEOHTMLParser(HTMLParser):
    """Custom HTML parser that extracts SEO-relevant elements."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.meta_keywords = ""
        self.canonical = ""
        self.og_tags = {}
        self.twitter_tags = {}
        self.headings = {f"h{i}": [] for i in range(1, 7)}
        self.images = []
        self.links = []
        self.current_heading_tag = None
        self.current_heading_text = ""
        self.in_title = False
        self.in_heading = False
        self.noindex = False
        self.nofollow = False
        self.has_viewport = False
        self.has_favicon = False
        self.has_json_ld = False
        self._title_data = ""
        self._heading_data = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag_lower = tag.lower()

        if tag_lower == "title":
            self.in_title = True
            self._title_data = ""

        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.in_heading = True
            self.current_heading_tag = tag_lower
            self._heading_data = ""

        elif tag_lower == "meta":
            name = attrs_dict.get("name", "").lower()
            property_ = attrs_dict.get("property", "").lower()
            content = attrs_dict.get("content", "")

            if name == "description":
                self.meta_description = content
            elif name == "keywords":
                self.meta_keywords = content
            elif name == "robots":
                robots = content.lower()
                if "noindex" in robots:
                    self.noindex = True
                if "nofollow" in robots:
                    self.nofollow = True
            elif name == "viewport":
                self.has_viewport = True
            elif property_.startswith("og:"):
                self.og_tags[property_] = content
            elif name.startswith("twitter:") or property_.startswith("twitter:"):
                key = name or property_
                self.twitter_tags[key] = content

        elif tag_lower == "link":
            rel = attrs_dict.get("rel", "").lower()
            if "canonical" in rel:
                self.canonical = attrs_dict.get("href", "")
            if "icon" in rel or "shortcut icon" in rel:
                self.has_favicon = True

        elif tag_lower == "img":
            src = attrs_dict.get("src", "")
            alt = attrs_dict.get("alt", "")
            loading = attrs_dict.get("loading", "")
            self.images.append({"src": src, "alt": alt, "lazy": loading == "lazy"})

        elif tag_lower == "a":
            href = attrs_dict.get("href", "")
            self.links.append(href)

        elif tag_lower == "script":
            type_ = attrs_dict.get("type", "")
            if type_ == "application/ld+json":
                self.has_json_ld = True

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower == "title":
            self.in_title = False
            self.title = self._title_data.strip()
        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6") and self.in_heading:
            self.in_heading = False
            if self.current_heading_tag:
                self.headings[self.current_heading_tag].append(self._heading_data.strip())
            self.current_heading_tag = None

    def handle_data(self, data):
        if self.in_title:
            self._title_data += data
        elif self.in_heading:
            self._heading_data += data


class SEOTool:
    """Real SEO analysis tool using HTML parsing and page fetching."""

    def __init__(self):
        self._browser = None

    def _get_browser(self):
        if self._browser is None:
            try:
                from .browser_tool import BrowserTool

                self._browser = BrowserTool()
            except Exception:
                logger.exception("SEO browser init failed")
        return self._browser

    def _fetch_html(self, url):
        browser = self._get_browser()
        if browser:
            try:
                nav = browser.navigate(url)
                if isinstance(nav, dict) and nav.get("error"):
                    logger.debug(f"Browser navigate failed for {url}: {nav.get('error')}")
                else:
                    content = browser.get_content()
                    text = (content.get("text", "") if isinstance(content, dict) else content) or ""
                    if text:
                        return text
            except Exception as e:
                logger.warning(f"Browser fetch failed for {url}: {e}")
            finally:
                # playwright's sync API leaves a RUNNING event loop registered
                # in this thread until stop() — always close after a fetch so
                # we never poison asyncio for later callers (chat streams etc).
                try:
                    browser.close()
                except Exception:
                    logger.debug("SEO browser close failed", exc_info=True)
                self._browser = None

        try:
            # SSRF protection: validates URL + full redirect chain
            from ..core.safe_http import safe_requests_get

            resp = safe_requests_get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (compatible; ORBIT-SEO/1.0)"})
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.warning(f"HTTP fetch failed for {url}: {e}")
            return ""

    def _parse_html(self, html):
        parser = SEOHTMLParser()
        try:
            parser.feed(html)
        except Exception as e:
            logger.warning(f"HTML parse error: {e}")
        return parser

    def check_meta_tags(self, html):
        parsed = self._parse_html(html)
        results = {"pass": [], "warn": [], "fail": []}

        title = parsed.title
        if not title:
            results["fail"].append("Missing <title> tag")
        elif len(title) < 30:
            results["warn"].append(f"Title too short ({len(title)} chars) — aim for 50-60")
        elif len(title) > 60:
            results["warn"].append(f"Title too long ({len(title)} chars) — aim for 50-60")
        else:
            results["pass"].append(f"Title length OK ({len(title)} chars)")
        results["title"] = title

        desc = parsed.meta_description
        if not desc:
            results["fail"].append("Missing meta description")
        elif len(desc) < 120:
            results["warn"].append(f"Meta description short ({len(desc)} chars) — aim for 150-160")
        elif len(desc) > 160:
            results["warn"].append(f"Meta description long ({len(desc)} chars) — aim for 150-160")
        else:
            results["pass"].append(f"Meta description length OK ({len(desc)} chars)")
        results["meta_description"] = desc

        if parsed.canonical:
            results["pass"].append(f"Canonical tag present: {parsed.canonical}")
        else:
            results["warn"].append("No canonical tag found")
        results["canonical"] = parsed.canonical

        og_required = ["og:title", "og:description", "og:image"]
        og_missing = [k for k in og_required if k not in parsed.og_tags]
        if not og_missing:
            results["pass"].append("Open Graph tags complete")
        else:
            results["warn"].append(f"Missing OG tags: {', '.join(og_missing)}")
        results["og_tags"] = parsed.og_tags

        if parsed.twitter_tags:
            results["pass"].append("Twitter Card tags present")
        else:
            results["warn"].append("No Twitter Card tags found")
        results["twitter_tags"] = parsed.twitter_tags

        return results

    def audit_headings(self, html):
        parsed = self._parse_html(html)
        results = {"pass": [], "warn": [], "fail": []}

        h1_count = len(parsed.headings.get("h1", []))
        if h1_count == 0:
            results["fail"].append("No H1 tag found")
        elif h1_count == 1:
            results["pass"].append("Single H1 tag (good)")
        else:
            results["warn"].append(f"Multiple H1 tags ({h1_count}) — use only one")
        results["h1_count"] = h1_count
        results["h1_texts"] = parsed.headings.get("h1", [])

        prev_level = 0
        skipped = False
        for level in range(1, 7):
            tag = f"h{level}"
            count = len(parsed.headings.get(tag, []))
            if count > 0 and prev_level > 0 and level > prev_level + 1:
                skipped = True
                results["warn"].append(f"Heading level skipped: h{prev_level} → h{level}")
            if count > 0:
                prev_level = level
        if not skipped:
            results["pass"].append("Heading hierarchy is properly nested")

        all_headings = []
        for level in range(1, 7):
            for text in parsed.headings.get(f"h{level}", []):
                all_headings.append(f"h{level}: {text}")
        results["headings"] = all_headings

        return results

    def analyze_images(self, html):
        parsed = self._parse_html(html)
        results = {"pass": [], "warn": [], "fail": []}

        total = len(parsed.images)
        no_alt = [img for img in parsed.images if not img["alt"].strip()]
        no_lazy = [img for img in parsed.images if not img["lazy"] and total > 5]

        results["total"] = total
        results["missing_alt"] = len(no_alt)
        results["no_lazy_loading"] = len(no_lazy)

        if total == 0:
            results["pass"].append("No images found")
        elif len(no_alt) == 0:
            results["pass"].append(f"All {total} images have alt text")
        else:
            results["fail"].append(f"{len(no_alt)}/{total} images missing alt text")

        if total > 5 and len(no_lazy) > 0:
            results["warn"].append(f"{len(no_lazy)} images without lazy loading")

        results["images"] = parsed.images[:20]
        return results

    def check_technical(self, url, html):
        parsed = self._parse_html(html)
        results = {"pass": [], "warn": [], "fail": []}

        if url.startswith("https://"):
            results["pass"].append("HTTPS enabled")
        else:
            results["fail"].append("Not using HTTPS")

        if parsed.has_viewport:
            results["pass"].append("Viewport meta tag present (mobile-friendly)")
        else:
            results["warn"].append("No viewport meta tag (may not be mobile-friendly)")

        if parsed.noindex:
            results["fail"].append("Page has noindex directive — won't appear in search results")
        if parsed.nofollow:
            results["warn"].append("Page has nofollow directive")

        if parsed.has_favicon:
            results["pass"].append("Favicon present")
        else:
            results["warn"].append("No favicon found")

        if parsed.has_json_ld:
            results["pass"].append("Structured data (JSON-LD) found")
        else:
            results["warn"].append("No structured data (JSON-LD) found")

        domain = urlparse(url).netloc
        internal = [l for l in parsed.links if domain in l]
        external = [l for l in parsed.links if l.startswith("http") and domain not in l]
        results["internal_links"] = len(internal)
        results["external_links"] = len(external)

        if len(internal) < 3:
            results["warn"].append(f"Few internal links ({len(internal)})")
        else:
            results["pass"].append(f"Good internal linking ({len(internal)} links)")

        return results

    def keyword_density(self, text, target_keyword=None):
        words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        total = len(words)
        freq = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1

        top_words = sorted(freq.items(), key=lambda x: -x[1])[:30]

        results = {
            "total_words": total,
            "unique_words": len(freq),
            "top_words": top_words[:20],
        }

        if target_keyword:
            kw_lower = target_keyword.lower()
            kw_count = text.lower().count(kw_lower)
            kw_words = len(kw_lower.split())
            density = (kw_count / max(total, 1)) * 100
            results["target_keyword"] = target_keyword
            results["keyword_count"] = kw_count
            results["keyword_density_pct"] = round(density, 2)
            if density < 0.5:
                results["advice"] = f"Keyword density low ({density:.2f}%) — aim for 1-2%"
            elif density > 2.5:
                results["advice"] = f"Keyword density high ({density:.2f}%) — may be keyword stuffing"
            else:
                results["advice"] = f"Keyword density good ({density:.2f}%)"

        return results

    def analyze_page(self, url):
        html = self._fetch_html(url)
        if not html:
            return {"error": f"Could not fetch page at {url}"}

        meta = self.check_meta_tags(html)
        headings = self.audit_headings(html)
        images = self.analyze_images(html)
        technical = self.check_technical(url, html)

        pass_count = len(meta["pass"]) + len(headings["pass"]) + len(images["pass"]) + len(technical["pass"])
        total_checks = (
            pass_count + len(meta["fail"]) + len(headings["fail"]) + len(images["fail"]) + len(technical["fail"])
        )
        score = int((pass_count / max(total_checks, 1)) * 100)

        return {
            "url": url,
            "score": score,
            "meta_tags": meta,
            "headings": headings,
            "images": images,
            "technical": technical,
            "total_checks": total_checks,
            "passed": pass_count,
            "issues": len(meta["fail"]) + len(headings["fail"]) + len(images["fail"]) + len(technical["fail"]),
            "warnings": len(meta["warn"]) + len(headings["warn"]) + len(images["warn"]) + len(technical["warn"]),
        }

    def full_audit(self, url):
        analysis = self.analyze_page(url)
        if "error" in analysis:
            return analysis

        lines = [f"SEO Audit Report: {url}", f"Score: {analysis['score']}/100", ""]

        meta = analysis["meta_tags"]
        lines.append("== Meta Tags ==")
        for item in meta["pass"]:
            lines.append(f"  + {item}")
        for item in meta["warn"]:
            lines.append(f"  ! {item}")
        for item in meta["fail"]:
            lines.append(f"  X {item}")

        headings = analysis["headings"]
        lines.append("\n== Headings ==")
        for item in headings["pass"]:
            lines.append(f"  + {item}")
        for item in headings["warn"]:
            lines.append(f"  ! {item}")
        for item in headings["fail"]:
            lines.append(f"  X {item}")
        if headings.get("headings"):
            lines.append("  Found:")
            for h in headings["headings"][:10]:
                lines.append(f"    {h}")

        images = analysis["images"]
        lines.append("\n== Images ==")
        for item in images["pass"]:
            lines.append(f"  + {item}")
        for item in images["warn"]:
            lines.append(f"  ! {item}")
        for item in images["fail"]:
            lines.append(f"  X {item}")

        tech = analysis["technical"]
        lines.append("\n== Technical ==")
        for item in tech["pass"]:
            lines.append(f"  + {item}")
        for item in tech["warn"]:
            lines.append(f"  ! {item}")
        for item in tech["fail"]:
            lines.append(f"  X {item}")
        lines.append(f"  Internal links: {tech.get('internal_links', 0)}")
        lines.append(f"  External links: {tech.get('external_links', 0)}")

        return {"report": "\n".join(lines), "analysis": analysis}
