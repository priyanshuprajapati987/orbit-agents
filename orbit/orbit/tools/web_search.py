# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Web Search Tool

Searches the web using multiple providers:
- DuckDuckGo (default, no API key needed)
- Google Custom Search (requires API key)
- Bing Search (requires API key)
"""

import requests
from typing import Dict
from ..logging_config import get_logger

logger = get_logger(__name__)


class WebSearchTool:
    def __init__(self):
        self.name = "web_search"
        self.description = "Search the web for information"
        self.results_cache = {}

    def search(self, query: str, num_results: int = 5, engine: str = "duckduckgo") -> Dict:
        try:
            if engine == "duckduckgo":
                return self._search_duckduckgo(query, num_results)
            elif engine == "google":
                return self._search_google(query, num_results)
            elif engine == "bing":
                return self._search_bing(query, num_results)
            else:
                return self._search_duckduckgo(query, num_results)
        except Exception as e:
            logger.error("Web search failed: %s", e)
            return {"error": str(e), "results": []}

    def _search_duckduckgo(self, query: str, num_results: int) -> Dict:
        try:
            url = "https://api.duckduckgo.com/"
            params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
            from ..core.ssrf_guard import get_ssrf_guard

            guard = get_ssrf_guard()
            if not guard.is_safe_url(url):
                return {"error": "SSRF blocked", "results": []}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                if data.get("AbstractText"):
                    results.append(
                        {
                            "title": data.get("Heading", ""),
                            "snippet": data.get("AbstractText", ""),
                            "url": data.get("AbstractURL", ""),
                            "source": data.get("AbstractSource", ""),
                        }
                    )
                for topic in data.get("RelatedTopics", [])[:num_results]:
                    if isinstance(topic, dict) and "Text" in topic:
                        results.append(
                            {
                                "title": topic.get("Text", "")[:100],
                                "snippet": topic.get("Text", ""),
                                "url": topic.get("FirstURL", ""),
                                "source": "DuckDuckGo",
                            }
                        )
                return {"query": query, "results": results[:num_results], "engine": "duckduckgo"}
        except Exception as e:
            logger.warning("DuckDuckGo search failed: %s", e)

        return self._search_html_scrape(query, num_results)

    def _search_html_scrape(self, query: str, num_results: int) -> Dict:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            url = "https://html.duckduckgo.com/html/"
            from ..core.ssrf_guard import get_ssrf_guard

            guard = get_ssrf_guard()
            if not guard.is_safe_url(url):
                return {"error": "SSRF blocked", "results": []}
            resp = requests.post(url, data={"q": query}, headers=headers, timeout=10)
            if resp.status_code == 200:
                from html.parser import HTMLParser

                class ResultParser(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.results = []
                        self._in_result = False
                        self._in_title = False
                        self._in_snippet = False
                        self._current = {}

                    def handle_starttag(self, tag, attrs):
                        attrs_dict = dict(attrs)
                        if tag == "a" and "result__a" in attrs_dict.get("class", ""):
                            self._in_title = True
                            self._current["url"] = attrs_dict.get("href", "")
                            self._current["title"] = ""
                        if tag == "a" and "result__snippet" in attrs_dict.get("class", ""):
                            self._in_snippet = True
                            self._current["snippet"] = ""

                    def handle_endtag(self, tag):
                        if tag == "a" and self._in_title:
                            self._in_title = False
                        if tag == "a" and self._in_snippet:
                            self._in_snippet = False
                            if self._current.get("title"):
                                self.results.append(dict(self._current))
                                self._current = {}

                    def handle_data(self, data):
                        if self._in_title:
                            self._current["title"] = self._current.get("title", "") + data
                        if self._in_snippet:
                            self._current["snippet"] = self._current.get("snippet", "") + data

                parser = ResultParser()
                parser.feed(resp.text)
                for r in parser.results[:num_results]:
                    r["source"] = "DuckDuckGo"
                return {"query": query, "results": parser.results[:num_results], "engine": "duckduckgo_html"}
        except Exception as e:
            logger.warning("HTML scrape search failed: %s", e)
        return {"query": query, "results": [], "engine": "none", "error": "All search engines failed"}

    def _search_google(self, query: str, num_results: int) -> Dict:
        import os

        api_key = os.getenv("GOOGLE_API_KEY", "")
        cse_id = os.getenv("GOOGLE_CSE_ID", "")
        if not api_key or not cse_id:
            return {"query": query, "results": [], "error": "Google API key not configured", "engine": "google"}
        try:
            url = "https://www.googleapis.com/customsearch/v1"
            params = {"key": api_key, "cx": cse_id, "q": query, "num": min(num_results, 10)}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("items", [])[:num_results]:
                    results.append(
                        {
                            "title": item.get("title", ""),
                            "snippet": item.get("snippet", ""),
                            "url": item.get("link", ""),
                            "source": "Google",
                        }
                    )
                return {"query": query, "results": results, "engine": "google"}
        except Exception as e:
            logger.error("Google search failed: %s", e)
        return {"query": query, "results": [], "engine": "google", "error": "Google search failed"}

    def _search_bing(self, query: str, num_results: int) -> Dict:
        import os

        api_key = os.getenv("BING_API_KEY", "")
        if not api_key:
            return {"query": query, "results": [], "error": "Bing API key not configured", "engine": "bing"}
        try:
            url = "https://api.bing.microsoft.com/v7.0/search"
            headers = {"Ocp-Apim-Subscription-Key": api_key}
            params = {"q": query, "count": min(num_results, 50)}
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("webPages", {}).get("value", [])[:num_results]:
                    results.append(
                        {
                            "title": item.get("name", ""),
                            "snippet": item.get("snippet", ""),
                            "url": item.get("url", ""),
                            "source": "Bing",
                        }
                    )
                return {"query": query, "results": results, "engine": "bing"}
        except Exception as e:
            logger.error("Bing search failed: %s", e)
        return {"query": query, "results": [], "engine": "bing", "error": "Bing search failed"}

    def get_news(self, topic: str = "technology", num_results: int = 5) -> Dict:
        try:
            import feedparser

            feeds = {
                "technology": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB",
                "world": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB",
                "business": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB",
                "science": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRAPTVtU0FtVnVHZ0pWVXlnQVAB",
            }
            feed_url = feeds.get(topic, feeds["technology"])
            feed = feedparser.parse(feed_url)
            results = []
            for entry in feed.entries[:num_results]:
                results.append(
                    {
                        "title": entry.get("title", ""),
                        "url": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "source": entry.get("source", {}).get("title", "Google News")
                        if isinstance(entry.get("source"), dict)
                        else "Google News",
                    }
                )
            return {"topic": topic, "results": results, "engine": "google_news_rss"}
        except ImportError:
            return {"topic": topic, "results": [], "error": "feedparser not installed"}
        except Exception as e:
            logger.error("News fetch failed: %s", e)
            return {"topic": topic, "results": [], "error": str(e)}
