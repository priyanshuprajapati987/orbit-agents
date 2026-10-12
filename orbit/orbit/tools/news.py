# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import defusedxml.ElementTree as ElementTree

import re

import requests

RSS_FEEDS = {
    "world": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "india": [
        "https://feeds.bbci.co.uk/news/world/asia/india/rss.xml",
    ],
    "tech": [
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "https://www.theverge.com/rss/index.xml",
    ],
    "finance": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ],
    "geopolitics": [
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "cyber": [
        "https://feeds.feedburner.com/TheHackersNews",
        "https://www.bleepingcomputer.com/feed/",
    ],
    "military": [
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    ],
    "energy": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ],
}


_XMLNS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


class NewsTool:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            }
        )

    def fetch_rss(self, category: str = "world", limit: int = 5) -> dict:
        urls = RSS_FEEDS.get(category, RSS_FEEDS["world"])
        articles = []

        for url in urls:
            try:
                from ..core.ssrf_guard import get_ssrf_guard

                guard = get_ssrf_guard()
                if not guard.is_safe_url(url):
                    continue
                resp = self.session.get(url, timeout=8)
                if resp.status_code != 200:
                    continue
                # Security: limit XML size to prevent billion-laughs attack
                if len(resp.content) > 1_000_000:  # 1MB max
                    continue
                root = ElementTree.fromstring(resp.content)

                items = root.iter("item")
                found = False
                for item in items:
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    desc = item.findtext("description", "")
                    pubdate = item.findtext("pubDate", "")
                    if title:
                        articles.append(
                            {
                                "title": title.strip(),
                                "link": link.strip(),
                                "description": desc.strip()[:200],
                                "date": pubdate.strip(),
                                "source": url,
                            }
                        )
                        found = True

                if not found:
                    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                        title = entry.findtext("{http://www.w3.org/2005/Atom}title", "")
                        link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                        link = link_el.get("href", "") if link_el is not None else ""
                        desc_el = entry.find("{http://www.w3.org/2005/Atom}summary")
                        desc = desc_el.text if desc_el is not None and desc_el.text else ""
                        pubdate = entry.findtext("{http://www.w3.org/2005/Atom}published", "")
                        if title:
                            articles.append(
                                {
                                    "title": title.strip(),
                                    "link": link.strip(),
                                    "description": desc.strip()[:200],
                                    "date": pubdate.strip(),
                                    "source": url,
                                }
                            )

            except Exception:
                continue

        return {
            "category": category,
            "count": len(articles),
            "articles": articles[:limit],
        }

    def list_categories(self) -> list:
        return list(RSS_FEEDS.keys())

    def handle_natural(self, text: str) -> str:
        text_lower = text.lower()

        category_map = {
            "geopolitical": "geopolitics",
            "geo": "geopolitics",
            "military": "military",
            "army": "military",
            "defence": "military",
            "defense": "military",
            "cyber": "cyber",
            "hack": "cyber",
            "security": "cyber",
            "energy": "energy",
            "oil": "energy",
            "gas": "energy",
            "tech": "tech",
            "technology": "tech",
            "ai": "tech",
            "finance": "finance",
            "stock": "finance",
            "market": "finance",
            "business": "finance",
            "india": "india",
        }

        category = "world"
        for keyword, cat in category_map.items():
            # Word-boundary match for short tokens: bare "ai" matches
            # "said"/"rain"/"plain"/"explain" and hijacks world news to tech.
            if len(keyword) <= 3:
                if re.search(r"\b" + re.escape(keyword) + r"\b", text_lower):
                    category = cat
                    break
            elif keyword in text_lower:
                category = cat
                break

        result = self.fetch_rss(category)
        if not result["articles"]:
            return f"🌍 {category.upper()} headlines currently unavailable. Try again later or check internet."

        lines = [f"🌍 {category.upper()} HEADLINES:"]
        for a in result["articles"][:6]:
            lines.append(f"• {a['title']}")
        if result["count"] > 6:
            lines.append(f"\n📌 Total {result['count']} articles found.")
        return "\n".join(lines)
