# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Tech Scout — Discovers trending AI/ML repos, models, and papers.

Sources:
- GitHub Trending (AI/ML category)
- HuggingFace new models (last 7 days)
- arXiv new papers (cs.AI, cs.LG)

Filters by ORBIT keywords, scores relevance, saves daily reports.
"""

from .runtime_paths import data_dir
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

from ..logging_config import get_logger

logger = get_logger(__name__)

ORBIT_KEYWORDS = [
    "agent",
    "memory",
    "llm",
    "voice",
    "automation",
    "orchestration",
    "rag",
    "multiagent",
    "multi-agent",
    "tool",
    "reasoning",
    "planning",
    "embedding",
    "fine-tuning",
    "deployment",
    "monitoring",
    "evaluation",
    "prompt",
    "chain",
    "graph",
    "workflow",
    "pipeline",
]

DATA_DIR = data_dir() / "scout_reports"


class TechScout:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ORBIT-TechScout/2.0",
                "Accept": "application/json",
            }
        )

    def scan_all(self) -> Dict:
        report = {
            "timestamp": datetime.now().isoformat(),
            "github_trending": [],
            "huggingface_models": [],
            "arxiv_papers": [],
            "summary": {},
        }

        report["github_trending"] = self._scan_github_trending()
        report["huggingface_models"] = self._scan_huggingface()
        report["arxiv_papers"] = self._scan_arxiv()

        all_items = report["github_trending"] + report["huggingface_models"] + report["arxiv_papers"]

        report["summary"] = {
            "total_items": len(all_items),
            "github_count": len(report["github_trending"]),
            "huggingface_count": len(report["huggingface_models"]),
            "arxiv_count": len(report["arxiv_papers"]),
            "avg_relevance": round(sum(i.get("relevance_score", 0) for i in all_items) / max(len(all_items), 1), 1),
            "top_relevant": sorted(all_items, key=lambda x: x.get("relevance_score", 0), reverse=True)[:5],
        }

        self._save_report(report)
        return report

    def _scan_github_trending(self) -> List[Dict]:
        results = []
        try:
            url = "https://api.github.com/search/repositories"
            since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            params = {
                "q": f"topic:ai OR topic:llm OR topic:agent created:>{since} stars:>50",
                "sort": "stars",
                "order": "desc",
                "per_page": 20,
            }
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                for repo in data.get("items", []):
                    name = repo.get("full_name", "")
                    desc = repo.get("description", "") or ""
                    topics = [t.lower() for t in repo.get("topics", [])]
                    text = f"{name} {desc} {' '.join(topics)}"
                    score = self._score_relevance(text)
                    if score >= 15:
                        results.append(
                            {
                                "source": "github",
                                "title": name,
                                "description": desc[:200],
                                "url": repo.get("html_url", ""),
                                "stars": repo.get("stargazers_count", 0),
                                "language": repo.get("language", ""),
                                "topics": topics[:10],
                                "relevance_score": score,
                                "created_at": repo.get("created_at", ""),
                            }
                        )
        except Exception as e:
            logger.error("GitHub trending scan failed: %s", e)
        return results[:15]

    def _scan_huggingface(self) -> List[Dict]:
        results = []
        try:
            (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
            url = "https://huggingface.co/api/models?sort=lastModified&direction=-1&limit=30&filter=text-generation"
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                models = resp.json()
                for model in models:
                    name = model.get("modelId", "")
                    tags = model.get("tags", [])
                    text = f"{name} {' '.join(tags)}"
                    score = self._score_relevance(text)
                    if score >= 15:
                        results.append(
                            {
                                "source": "huggingface",
                                "title": name,
                                "description": f"Tags: {', '.join(tags[:8])}",
                                "url": f"https://huggingface.co/{name}",
                                "downloads": model.get("downloads", 0),
                                "likes": model.get("likes", 0),
                                "tags": tags[:10],
                                "relevance_score": score,
                                "last_modified": model.get("lastModified", ""),
                            }
                        )
        except Exception as e:
            logger.error("HuggingFace scan failed: %s", e)
        return results[:15]

    def _scan_arxiv(self) -> List[Dict]:
        results = []
        try:
            query = "cat:cs.AI+OR+cat:cs.LG"
            url = (
                f"https://export.arxiv.org/api/query?search_query={query}"
                f"&sortBy=submittedDate&sortOrder=descending&max_results=20"
            )
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                entries = resp.text.split("<entry>")[1:]
                for entry in entries:
                    title = self._extract_xml(entry, "title").replace("\n", " ").strip()
                    summary = self._extract_xml(entry, "summary").replace("\n", " ").strip()
                    link = self._extract_xml(entry, "id")
                    authors = self._extract_xml(entry, "author")
                    text = f"{title} {summary}"
                    score = self._score_relevance(text)
                    if score >= 15:
                        results.append(
                            {
                                "source": "arxiv",
                                "title": title[:150],
                                "description": summary[:200],
                                "url": link,
                                "authors": authors[:100],
                                "relevance_score": score,
                            }
                        )
        except Exception as e:
            logger.error("arXiv scan failed: %s", e)
        return results[:15]

    def _score_relevance(self, text: str) -> float:
        text_lower = text.lower()
        hits = sum(1 for kw in ORBIT_KEYWORDS if kw in text_lower)
        score = min(100, hits * 8 + (2 if hits >= 3 else 0) + (5 if hits >= 5 else 0))
        return score

    def _extract_xml(self, xml: str, tag: str) -> str:
        pattern = f"<{tag}[^>]*>(.*?)</{tag}>"
        match = re.search(pattern, xml, re.DOTALL)
        return match.group(1).strip() if match else ""

    def _save_report(self, report: Dict):
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = DATA_DIR / f"{date_str}.json"
        try:
            existing = []
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    existing = [existing]
            existing.append(report)
            path.write_text(json.dumps(existing, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            logger.info("Scout report saved: %s (%d items)", path, report["summary"]["total_items"])
        except Exception as e:
            logger.error("Failed to save scout report: %s", e)

    def get_latest(self) -> Optional[Dict]:
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = DATA_DIR / f"{date_str}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data[-1] if isinstance(data, list) else data
            except Exception:
                pass
        reports = self.get_history(1)
        return reports[0] if reports else None

    def get_history(self, days: int = 7) -> List[Dict]:
        try:
            days = int(days)
        except (ValueError, TypeError):
            days = 7
        days = max(1, min(days, 90))
        reports = []
        for i in range(days):
            date = datetime.now() - timedelta(days=i)
            path = DATA_DIR / f"{date.strftime('%Y-%m-%d')}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        reports.extend(data)
                    else:
                        reports.append(data)
                except Exception:
                    pass
        return reports


_scout: Optional[TechScout] = None


def get_scout() -> TechScout:
    global _scout
    if _scout is None:
        _scout = TechScout()
    return _scout
