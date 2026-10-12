# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""Research, Writing, SEO, FineTune, and Thumbnail agents."""

import logging
import re

from ..config import ORBIT_SYSTEM_PROMPT
from ..core.llm import legacy_adapter as llm_router
from .base import BaseAgent

logger = logging.getLogger(__name__)


class ResearchAgent(BaseAgent):
    label = "RES"
    name = "Research"
    color = "#f59e0b"
    description = "News, web research, information gathering"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the RESEARCH specialist - the eyes and ears of the system.

Your capabilities:
- Search the web for information on any topic
- Fetch and aggregate news from multiple sources
- Summarize articles, papers, and reports
- Compare products, technologies, or approaches
- Answer factual questions with citations
- Track trending topics and breaking news
- Research companies, people, and organizations

Rules:
- ALWAYS use WebSearchTool for web queries
- ALWAYS use NewsTool for news/headlines
- Provide structured responses: key facts, bullet points, comparisons
- Include sources/URLs when available
- Distinguish between facts and opinions
- For "what is X" questions: give a concise definition + key details
- For "latest news" requests: show top 5-10 headlines with dates
- For "compare X vs Y": use a table format with pros/cons
- If search fails, say so honestly - don't make up information
- For "who invented X" or "when was X": provide the answer + context"""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._tool = None
        self._search_tool = None

    def _get_tool(self):
        if self._tool is None:
            from ..tools import NewsTool

            self._tool = NewsTool()
        return self._tool

    def _get_search_tool(self):
        if self._search_tool is None:
            from ..tools.web_search import WebSearchTool

            self._search_tool = WebSearchTool()
        return self._search_tool

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "news",
            "headline",
            "happening",
            "world",
            "update",
            "today",
            "latest",
            "current",
            "search",
            "find",
            "research",
            "khabar",
            "web search",
            "google",
            "lookup",
        ]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        if intent == "news":
            return score >= 1
        return score >= 2

    def _run(self, text, context=None):
        text_lower = text.lower()
        try:
            tool = self._get_tool()
            if tool and ("news" in text_lower or "headline" in text_lower):
                result = tool.handle_natural(text)
                if result:
                    return result
        except Exception:
            pass
        if "search" in text_lower or "google" in text_lower or "lookup" in text_lower or "web" in text_lower:
            query = re.sub(r"\b(search|google|lookup|web|for|the|about)\b", "", text_lower).strip()
            if not query:
                query = text
            search_tool = self._get_search_tool()
            search_result = search_tool.search(query, num_results=5)
            if not search_result or not isinstance(search_result, dict):
                return f"Search failed for '{query}'. Tool returned: {search_result}"
            results = search_result.get("results", [])
            if results:
                lines = [f"Search results for '{query}':\n"]
                for i, r in enumerate(results, 1):
                    lines.append(f"{i}. **{r.get('title', 'No title')}**")
                    lines.append(f"   {r.get('snippet', '')[:200]}")
                    lines.append(f"   {r.get('url', '')}")
                    lines.append("")
                return "\n".join(lines)
            return f"No search results found for '{query}'."
        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)


class WritingAgent(BaseAgent):
    label = "WRI"
    name = "Writing"
    color = "#ec4899"
    description = "Content creation, emails, messages, editing, save to file"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the WRITING specialist - the voice of the system.

Your capabilities:
- Write emails (professional, casual, formal tones)
- Create articles, blog posts, essays
- Draft proposals, reports, summaries
- Write creative content: stories, poems, scripts
- Translate between languages
- Rewrite/paraphrase content
- Generate social media posts
- Compose letters and messages

Rules:
- Match the tone to the audience (formal for business, casual for friends)
- Structure content with clear headers, paragraphs, and flow
- For emails: include subject line, greeting, body, sign-off
- For articles: include intro, body sections, conclusion
- For "translate to X": provide accurate translation, not word-for-word
- For "summarize": extract key points, keep it concise
- For "rewrite": improve clarity while preserving meaning
- If saving to file: use FileTools to write the file
- If the request is vague, ask for audience/purpose/tone before writing"""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._file_tool = None

    def _get_file_tool(self):
        if self._file_tool is None:
            from ..tools.file_tools import FileTools

            self._file_tool = FileTools()
        return self._file_tool

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = ["write", "draft", "email", "compose", "edit", "rewrite", "content", "blog", "article", "message"]
        text_lower = text.lower()
        score = sum(1 for kw in keywords if kw in text_lower)
        return score >= 2

    def _run(self, text, context=None):
        text_lower = text.lower()
        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"

        # Email tool integration
        if any(kw in text_lower for kw in ["send email", "send mail", "email to", "mail to"]):
            try:
                from ..tools.email_tool import EmailTool

                email_tool = EmailTool()
                if not email_tool.check_config().get("configured"):
                    return "⚠️ Email not configured. Set SMTP credentials in .env."
                import re as _re

                to_m = _re.search(r"(?:to|for)\s+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text_lower)
                to_email = to_m.group(1) if to_m else None
                if not to_email:
                    return "⚠️ Provide recipient email: 'send email to user@example.com'"
                subject_m = _re.search(r"subject\s+[`\"']?([^`\"'\n]+)", text)
                subject = subject_m.group(1).strip().rstrip(".,;:") if subject_m else "Message from ORBIT"
                body = llm_router.generate(text, history=self.history, system_prompt=sys_prompt)
                result = email_tool.send(to=to_email, subject=subject, body=body)
                if result and result.get("success"):
                    return f"✉️ Email sent to {to_email}"
                return f"⚠️ Email failed: {result}"
            except Exception as e:
                return f"⚠️ Email tool error: {e}"

        content = llm_router.generate(text, history=self.history, system_prompt=sys_prompt)

        save_match = re.search(r"(?:save|write|export|output)\s+(?:to\s+)?[`\"']?([^\s`\"']+\.\w+)[`\"']?", text, re.IGNORECASE)
        if save_match:
            filepath = save_match.group(1)
            tool = self._get_file_tool()
            if tool:
                try:
                    result = tool.write(filepath, content)
                    if result and result.get("success"):
                        return f"{content}\n\n---\nSaved to: {filepath}"
                    return f"{content}\n\n---\nFailed to save to {filepath}: {result}"
                except Exception as e:
                    return f"{content}\n\n---\nSave failed: {e}"

        return content


class SEOAgent(BaseAgent):
    label = "SEO"
    name = "SEO"
    color = "#22c55e"
    description = "SEO optimization, keyword research, page auditing, content analysis"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the SEO specialist.
Your job: Analyze content for SEO, audit pages, suggest keywords, optimize titles/meta descriptions.
Provide actionable SEO recommendations with specific implementation steps."""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._search_tool = None
        self._seo_tool = None

    def _get_search_tool(self):
        if self._search_tool is None:
            from ..tools.web_search import WebSearchTool

            self._search_tool = WebSearchTool()
        return self._search_tool

    def _get_seo_tool(self):
        if self._seo_tool is None:
            from ..tools.seo_tool import SEOTool

            self._seo_tool = SEOTool()
        return self._seo_tool

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "seo",
            "keyword",
            "meta",
            "title tag",
            "search engine",
            "ranking",
            "backlink",
            "serp",
            "optimize",
            "keyword research",
            "analyze page",
            "seo audit",
            "on-page",
            "onpage",
        ]
        text_lower = text.lower()
        return sum(1 for kw in keywords if kw in text_lower) >= 1

    def _run(self, text, context=None):
        text_lower = text.lower()
        search_tool = self._get_search_tool()
        seo_tool = self._get_seo_tool()

        url_match = re.search(r"(https?://[^\s`\"']+)", text)

        if (
            "analyze page" in text_lower
            or "seo audit" in text_lower
            or "on-page" in text_lower
            or "onpage" in text_lower
            or ("meta" in text_lower and url_match)
        ):
            if url_match:
                url = url_match.group(1)
                result = seo_tool.full_audit(url)
                if "error" in result:
                    return f"SEO audit failed: {result['error']}"
                return result.get("report", "Audit completed.")
            return "Provide a URL to analyze. Usage: analyze page https://example.com"

        if "keyword density" in text_lower or "keyword count" in text_lower:
            if url_match:
                url = url_match.group(1)
                html = seo_tool._fetch_html(url)
                if html:
                    parsed = seo_tool._parse_html(html)
                    text_content = parsed.get("text", "") if isinstance(parsed, dict) else re.sub(r"<[^>]+>", " ", html)
                    kw = re.sub(r"\b(keyword|density|count|for|the|about)\b", "", text_lower).strip()
                    result = seo_tool.keyword_density(text_content, kw if kw else None)
                    lines = [f"Keyword Density Analysis for {url}:", ""]
                    lines.append(f"Total words: {result['total_words']}")
                    lines.append(f"Unique words: {result['unique_words']}")
                    if "target_keyword" in result:
                        lines.append(f"\nTarget: '{result['target_keyword']}'")
                        lines.append(f"Count: {result['keyword_count']}")
                        lines.append(f"Density: {result['keyword_density_pct']}%")
                        lines.append(f"Advice: {result['advice']}")
                    lines.append("\nTop 20 words:")
                    for word, count in result.get("top_words", [])[:20]:
                        lines.append(f"  {word}: {count}")
                    return "\n".join(lines)
            return "Provide a URL and keyword. Usage: keyword density for keyword https://example.com"

        if "keyword research" in text_lower or "research keywords" in text_lower:
            query = re.sub(r"\b(keyword|research|for|the|about)\b", "", text_lower).strip()
            if not query:
                return "What topic should I research keywords for?"
            result = search_tool.search(f"{query} SEO keywords", num_results=5)
            if not result or not isinstance(result, dict):
                return f"Search failed for '{query}'."
            results = result.get("results", [])
            if results:
                keywords_found = set()
                for r in results:
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")
                    for word in (title + " " + snippet).split():
                        if len(word) > 4 and word.isalpha():
                            keywords_found.add(word.lower())
                kw_list = sorted(keywords_found)[:15]
                return (
                    f"Keyword research for '{query}':\n\nTop keywords:\n"
                    + "\n".join(f"  - {kw}" for kw in kw_list)
                    + f"\n\nBased on {len(results)} search results."
                )
            return f"No keyword data found for '{query}'."

        if "serp" in text_lower or "search results" in text_lower or "ranking" in text_lower:
            query = re.sub(r"\b(serp|search|results|ranking|for|the|about|analysis)\b", "", text_lower).strip()
            if not query:
                return "What query should I analyze SERP results for?"
            result = search_tool.search(query, num_results=10)
            if not result or not isinstance(result, dict):
                return f"Search failed for '{query}'."
            results = result.get("results", [])
            if results:
                lines = [f"SERP Analysis for '{query}':\n"]
                lines.append(f"Found {len(results)} results:\n")
                for i, r in enumerate(results, 1):
                    title = r.get("title", "No title")
                    url = r.get("url", "")
                    snippet = r.get("snippet", "")[:150]
                    lines.append(f"{i}. {title}")
                    lines.append(f"   {url}")
                    if snippet:
                        lines.append(f"   {snippet}")
                    lines.append("")
                all_words = set()
                for r in results:
                    for word in (r.get("title", "") + " " + r.get("snippet", "")).split():
                        if len(word) > 3 and word.isalpha():
                            all_words.add(word.lower())
                top_kw = sorted(all_words)[:10]
                if top_kw:
                    lines.append("Common keywords: " + ", ".join(top_kw))
                return "\n".join(lines)
            return f"No SERP data found for '{query}'."

        if "competitor" in text_lower or "competition" in text_lower or "compare" in text_lower:
            query = re.sub(r"\b(competitor|competition|compare|analysis|with|against)\b", "", text_lower).strip()
            if not query:
                return "What topic/brand should I analyze competitors for?"
            result = search_tool.search(f"{query} competitors alternatives", num_results=8)
            if not result or not isinstance(result, dict):
                return f"Search failed for '{query}'."
            results = result.get("results", [])
            if results:
                lines = [f"Competitor Analysis for '{query}':\n"]
                competitors = []
                for r in results:
                    title = r.get("title", "")
                    url = r.get("url", "")
                    snippet = r.get("snippet", "")[:200]
                    name = title.split(" - ")[0].split(" | ")[0].split(": ")[0].strip()
                    if name and len(name) > 2:
                        competitors.append({"name": name, "url": url, "snippet": snippet})
                for i, c in enumerate(competitors[:6], 1):
                    lines.append(f"{i}. {c['name']}")
                    lines.append(f"   {c['url']}")
                    if c["snippet"]:
                        lines.append(f"   {c['snippet']}")
                    lines.append("")
                if competitors:
                    names = [c["name"] for c in competitors[:5]]
                    lines.append(f"Top competitors: {', '.join(names)}")
                return "\n".join(lines)
            return f"No competitor data found for '{query}'."

        if "backlink" in text_lower:
            query = re.sub(r"\b(backlink|backlinks|analysis|check)\b", "", text_lower).strip()
            if not query:
                return "What domain should I check backlinks for?"
            result = search_tool.search(f'"{query}" backlinks referring domains', num_results=8)
            if not result or not isinstance(result, dict):
                return f"Search failed for '{query}'."
            results = result.get("results", [])
            if results:
                lines = [f"Backlink Analysis for '{query}':\n"]
                for i, r in enumerate(results[:5], 1):
                    lines.append(f"{i}. {r.get('title', '')}")
                    lines.append(f"   {r.get('url', '')}")
                    snippet = r.get("snippet", "")[:150]
                    if snippet:
                        lines.append(f"   {snippet}")
                    lines.append("")
                lines.append("Tip: For detailed backlink data, use Ahrefs, SEMrush, or Moz.")
                return "\n".join(lines)
            return f"No backlink data found for '{query}'."

        if url_match and ("check" in text_lower or "analyze" in text_lower or "audit" in text_lower):
            url = url_match.group(1)
            result = seo_tool.full_audit(url)
            if "error" in result:
                return f"SEO audit failed: {result['error']}"
            return result.get("report", "Audit completed.")

        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)


class FineTuneAgent(BaseAgent):
    label = "FTN"
    name = "Fine-tune"
    color = "#a855f7"
    description = "Model fine-tuning, training data preparation, evaluation"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the FINE-TUNING specialist.
Your job: Prepare training data, evaluate model performance, suggest fine-tuning strategies.
Help users fine-tune LLMs for specific tasks. Provide data formatting guidance."""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._file_tool = None
        self._search_tool = None

    def _get_file_tool(self):
        if self._file_tool is None:
            from ..tools.file_tools import FileTools

            self._file_tool = FileTools()
        return self._file_tool

    def _get_search_tool(self):
        if self._search_tool is None:
            from ..tools.web_search import WebSearchTool

            self._search_tool = WebSearchTool()
        return self._search_tool

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "fine-tune",
            "finetune",
            "train",
            "training data",
            "dataset",
            "model training",
            "evaluate model",
            "benchmark",
        ]
        text_lower = text.lower()
        return sum(1 for kw in keywords if kw in text_lower) >= 2

    def _run(self, text, context=None):
        text_lower = text.lower()

        if "save" in text_lower or "export" in text_lower or "write" in text_lower:
            save_match = re.search(r"(?:save|export|write)\s+(?:to\s+)?[`\"']?([^\s`\"']+\.\w+)[`\"']?", text, re.IGNORECASE)
            sys_prompt = self.DEFAULT_PROMPT
            if context:
                sys_prompt += f"\n\n[Context]\n{context}"
            content = llm_router.generate(text, history=self.history, system_prompt=sys_prompt)
            if save_match:
                filepath = save_match.group(1)
                ft = self._get_file_tool()
                if ft:
                    try:
                        result = ft.write(filepath, content)
                        if result and result.get("success"):
                            return f"{content}\n\n---\nSaved to: {filepath}"
                    except Exception:
                        pass
            return content

        if "research" in text_lower or "find" in text_lower or "search" in text_lower:
            query = re.sub(r"\b(research|find|search|for|the|about|best|practices)\b", "", text_lower).strip()
            if query:
                search = self._get_search_tool()
                result = search.search(f"{query} fine-tuning LLM training data", num_results=5)
                results_list = result.get("results", []) if isinstance(result, dict) else []
                if results_list:
                    lines = [f"Research on '{query}' fine-tuning:\n"]
                    for i, r in enumerate(results_list, 1):
                        lines.append(f"{i}. {r.get('title', '')}")
                        lines.append(f"   {r.get('snippet', '')[:200]}")
                        lines.append("")
                    research_text = "\n".join(lines)
                    sys_prompt = self.DEFAULT_PROMPT
                    if context:
                        sys_prompt += f"\n\n[Context]\n{context}"
                    sys_prompt += f"\n\n[Research Data]\n{research_text}"
                    return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)

        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)


class ThumbnailAgent(BaseAgent):
    label = "THB"
    name = "Thumbnail"
    color = "#f43f5e"
    description = "Thumbnail generation, image design suggestions"

    DEFAULT_PROMPT = f"""{ORBIT_SYSTEM_PROMPT}
You are the THUMBNAIL DESIGN specialist.
Your job: Suggest thumbnail designs, describe layouts, recommend text placement.
Provide detailed visual descriptions that can be used with image generation tools."""

    def __init__(self, assistant=None):
        super().__init__(assistant)
        self._file_tool = None
        self._search_tool = None

    def _get_file_tool(self):
        if self._file_tool is None:
            from ..tools.file_tools import FileTools

            self._file_tool = FileTools()
        return self._file_tool

    def _get_search_tool(self):
        if self._search_tool is None:
            from ..tools.web_search import WebSearchTool

            self._search_tool = WebSearchTool()
        return self._search_tool

    def can_handle(self, text, intent=None, confidence=0.0):
        keywords = [
            "thumbnail",
            "thumb",
            "banner",
            "cover image",
            "design",
            "poster",
            "yt thumbnail",
            "youtube thumbnail",
        ]
        text_lower = text.lower()
        return sum(1 for kw in keywords if kw in text_lower) >= 2

    def _run(self, text, context=None):
        text_lower = text.lower()

        if "save" in text_lower or "export" in text_lower or "write" in text_lower:
            save_match = re.search(r"(?:save|export|write)\s+(?:to\s+)?[`\"']?([^\s`\"']+\.\w+)[`\"']?", text, re.IGNORECASE)
            sys_prompt = self.DEFAULT_PROMPT
            if context:
                sys_prompt += f"\n\n[Context]\n{context}"
            content = llm_router.generate(text, history=self.history, system_prompt=sys_prompt)
            if save_match:
                filepath = save_match.group(1)
                ft = self._get_file_tool()
                if ft:
                    try:
                        result = ft.write(filepath, content)
                        if result and result.get("success"):
                            return f"{content}\n\n---\nSaved to: {filepath}"
                    except Exception:
                        pass
            return content

        if "reference" in text_lower or "inspiration" in text_lower or "examples" in text_lower:
            query = re.sub(r"\b(reference|inspiration|examples|for|the|about|find|show)\b", "", text_lower).strip()
            if query:
                search = self._get_search_tool()
                result = search.search(f"{query} thumbnail design inspiration", num_results=5)
                results_list = result.get("results", []) if isinstance(result, dict) else []
                if results_list:
                    lines = [f"Reference designs for '{query}':\n"]
                    for i, r in enumerate(results_list, 1):
                        lines.append(f"{i}. {r.get('title', '')}")
                        lines.append(f"   {r.get('url', '')}")
                        lines.append("")
                    return "\n".join(lines) + "\nUse these references to inform your design suggestions."

        sys_prompt = self.DEFAULT_PROMPT
        if context:
            sys_prompt += f"\n\n[Context]\n{context}"
        return llm_router.generate(text, history=self.history, system_prompt=sys_prompt)
