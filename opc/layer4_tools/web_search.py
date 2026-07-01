"""Web search and fetch tools."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import httpx

from opc.layer4_tools.output_budget import clip_text, persist_tool_result
from opc.layer4_tools.registry import ToolDefinition


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag in {"p", "br", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in {"p", "div", "section", "article", "li", "tr"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = " ".join(str(data or "").split())
        if text:
            self._parts.append(text + " ")

    def text(self) -> str:
        lines = []
        for raw in "".join(self._parts).splitlines():
            line = " ".join(raw.split())
            if line:
                lines.append(line)
        return "\n".join(lines)


def _html_to_text(value: str) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(value)
    return parser.text() or value


async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web using DuckDuckGo HTML scraping (no API key needed)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (compatible; OPC/1.0)"},
            )
            resp.raise_for_status()
            text = resp.text

            results: list[dict[str, str]] = []
            import re
            links = re.findall(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', text)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</[^>]+>', text, re.DOTALL)
            for i, (url, title) in enumerate(links[:max_results]):
                snippet = snippets[i].strip() if i < len(snippets) else ""
                snippet = re.sub(r"<[^>]+>", "", snippet).strip()
                title = re.sub(r"<[^>]+>", "", title).strip()
                results.append({"title": title, "url": url, "snippet": snippet})

            return {"results": results, "query": query}
    except Exception as e:
        return {"error": str(e), "query": query}


async def web_fetch(
    url: str,
    max_length: int = 20000,
    offset: int = 0,
    save_full: bool = True,
    task: Any | None = None,
) -> dict[str, Any]:
    """Fetch a URL and return its text content."""
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; OPC/1.0)"})
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text" in content_type or "json" in content_type or "xml" in content_type:
                text = _html_to_text(resp.text) if "html" in content_type else resp.text
                start = max(0, int(offset or 0))
                limit = max(1, int(max_length or 20000))
                sliced = text[start:]
                preview = clip_text(sliced, limit=limit, marker="web_fetch truncated")
                next_offset = start + preview.kept_chars if preview.truncated else None
                persisted = {}
                if save_full and (preview.truncated or start > 0):
                    persisted = persist_tool_result(
                        text,
                        tool_name="web_fetch",
                        task=task,
                        extension="txt",
                    )
                return {
                    "content": preview.text,
                    "url": str(resp.url),
                    "final_url": str(resp.url),
                    "status": resp.status_code,
                    "content_type": content_type,
                    "total_chars": len(text),
                    "offset": start,
                    "max_length": limit,
                    "truncated": preview.truncated,
                    "omitted_chars": preview.omitted_chars,
                    "next_offset": next_offset,
                    "full_content_path": persisted.get("full_output_path", ""),
                    "success": True,
                }
            else:
                return {"error": f"Unsupported content type: {content_type}", "url": str(resp.url)}
    except Exception as e:
        return {"error": str(e), "url": url}


def create_web_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="web_search",
            description="Search the web for information. Returns titles, URLs, and snippets.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
            func=web_search,
            category="search",
        ),
        ToolDefinition(
            name="web_fetch",
            description="Fetch a URL and return its text content.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "max_length": {"type": "integer", "description": "Max content length", "default": 20000},
                    "offset": {"type": "integer", "description": "Character offset to start reading from", "default": 0},
                    "save_full": {"type": "boolean", "description": "Persist full fetched text when preview is truncated", "default": True},
                },
                "required": ["url"],
            },
            func=web_fetch,
            category="search",
            self_bounded_output=True,
            max_result_chars=80_000,
        ),
    ]
