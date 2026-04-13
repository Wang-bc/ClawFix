from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolSpec


RESULT_PATTERN = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE,
)
TITLE_PATTERN = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text)


class WebToolClient:
    def __init__(
        self,
        *,
        timeout_s: int = 8,
        provider: str = "tavily",
        tavily_api_key: str = "",
        tavily_base_url: str = "https://api.tavily.com",
        tavily_search_depth: str = "advanced",
        tavily_extract_depth: str = "advanced",
        tavily_topic: str = "general",
    ) -> None:
        self.timeout_s = timeout_s
        self.provider = provider.strip().lower() or "tavily"
        self.tavily_api_key = tavily_api_key.strip()
        self.tavily_base_url = tavily_base_url.strip().rstrip("/") or "https://api.tavily.com"
        self.tavily_search_depth = tavily_search_depth.strip().lower() or "advanced"
        self.tavily_extract_depth = tavily_extract_depth.strip().lower() or "advanced"
        self.tavily_topic = tavily_topic.strip().lower() or "general"

    def search(
        self,
        query: str,
        limit: int = 5,
        search_depth: str = "",
        topic: str = "",
    ) -> dict[str, object]:
        if not query.strip():
            return {"results": []}
        if self.provider == "tavily":
            return self._search_tavily(
                query=query,
                limit=limit,
                search_depth=search_depth,
                topic=topic,
            )
        return self._search_duckduckgo(query=query, limit=limit)

    def fetch(
        self,
        url: str,
        max_chars: int = 1800,
        query: str = "",
        extract_depth: str = "",
    ) -> dict[str, object]:
        if self.provider == "tavily":
            return self._fetch_tavily(
                url=url,
                max_chars=max_chars,
                query=query,
                extract_depth=extract_depth,
            )
        return self._fetch_direct(url=url, max_chars=max_chars)

    def _search_tavily(
        self,
        *,
        query: str,
        limit: int,
        search_depth: str,
        topic: str,
    ) -> dict[str, object]:
        if not self.tavily_api_key:
            return {
                "results": [],
                "error": "TAVILY_API_KEY not configured",
                "provider": "tavily",
                "configured": False,
            }

        payload = {
            "query": query,
            "topic": (topic or self.tavily_topic).strip().lower() or self.tavily_topic,
            "search_depth": (search_depth or self.tavily_search_depth).strip().lower() or self.tavily_search_depth,
            "max_results": max(1, min(int(limit or 5), 10)),
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_favicon": False,
        }
        try:
            response = self._request_json("/search", payload)
        except Exception as exc:  # noqa: BLE001
            return {"results": [], "error": str(exc), "provider": "tavily", "configured": True}

        results: list[dict[str, object]] = []
        for item in response.get("results", []):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip() or url
            if not url or not title:
                continue
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": self._compact_text(str(item.get("content", "")).strip(), 320),
                    "score": float(item.get("score", 0.0) or 0.0),
                }
            )
        return {"results": results[: max(1, min(int(limit or 5), 10))], "provider": "tavily", "configured": True}

    def _fetch_tavily(
        self,
        *,
        url: str,
        max_chars: int,
        query: str,
        extract_depth: str,
    ) -> dict[str, object]:
        if not url.strip():
            return {"ok": False, "url": url, "error": "url is empty", "provider": "tavily"}
        if not self.tavily_api_key:
            return {"ok": False, "url": url, "error": "TAVILY_API_KEY not configured", "provider": "tavily"}

        payload: dict[str, Any] = {
            "urls": [url],
            "extract_depth": (extract_depth or self.tavily_extract_depth).strip().lower() or self.tavily_extract_depth,
            "format": "markdown",
            "include_images": False,
        }
        if query.strip():
            payload["query"] = query.strip()
            payload["chunks_per_source"] = 4
        try:
            response = self._request_json("/extract", payload)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "url": url, "error": str(exc), "provider": "tavily"}

        results = response.get("results", [])
        if not isinstance(results, list) or not results:
            return {"ok": False, "url": url, "error": "empty extract result", "provider": "tavily"}
        item = results[0] if isinstance(results[0], dict) else {}
        title = str(item.get("title", "")).strip() or url
        content = item.get("raw_content") or item.get("content") or ""
        if isinstance(content, list):
            content = "\n\n".join(str(block).strip() for block in content if str(block).strip())
        normalized = str(content).strip()
        return {
            "ok": True,
            "url": url,
            "title": title,
            "content": self._compact_text(normalized, max_chars),
            "truncated": len(normalized) > max_chars,
            "provider": "tavily",
        }

    def _search_duckduckgo(self, *, query: str, limit: int) -> dict[str, object]:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://duckduckgo.com/html/?q={encoded}"
        request = urllib.request.Request(url, headers={"User-Agent": "ClawFix/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset, errors="replace")
        except Exception as exc:  # noqa: BLE001
            return {"results": [], "error": str(exc), "provider": "duckduckgo_html"}

        results: list[dict[str, str]] = []
        for match in RESULT_PATTERN.finditer(body):
            href = self._unwrap_duckduckgo_url(html.unescape(match.group("href")))
            title = self._clean_html(match.group("title"))
            if not href:
                continue
            results.append({"title": title, "url": href})
            if len(results) >= limit:
                break
        return {"results": results, "provider": "duckduckgo_html"}

    def _fetch_direct(self, *, url: str, max_chars: int = 1800) -> dict[str, object]:
        request = urllib.request.Request(url, headers={"User-Agent": "ClawFix/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                content_type = response.headers.get_content_type()
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset, errors="replace")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "url": url, "error": str(exc), "provider": "duckduckgo_html"}

        title_match = TITLE_PATTERN.search(body)
        title = self._clean_html(title_match.group(1)) if title_match else url
        if content_type == "application/json":
            try:
                parsed = json.loads(body)
                content = json.dumps(parsed, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                content = body
        else:
            parser = _TextExtractor()
            parser.feed(body)
            content = "\n".join(parser.parts)
        content = re.sub(r"\n{3,}", "\n\n", content)
        return {
            "ok": True,
            "url": url,
            "title": title,
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
            "provider": "duckduckgo_html",
        }

    def _request_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.tavily_base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.tavily_api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected Tavily response type: {type(payload)!r}")
        return payload

    def _unwrap_duckduckgo_url(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if "uddg" in query:
            return urllib.parse.unquote(query["uddg"][0])
        return url

    def _clean_html(self, text: str) -> str:
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text).strip()

    def _compact_text(self, text: str, limit: int) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        return normalized[:limit]


def register_web_tools(registry: ToolRegistry, client: WebToolClient) -> None:
    registry.register(
        ToolSpec(
            name="web_search",
            description="杩涜杞婚噺 Web 鎼滅储锛岃繑鍥炴爣棰樺拰 URL銆?",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                    "search_depth": {"type": "string"},
                    "topic": {"type": "string"},
                },
            },
            safety_policy="network-read",
            handler=lambda payload: client.search(
                query=str(payload.get("query", "")),
                limit=int(payload.get("limit", 5)),
                search_depth=str(payload.get("search_depth", "")),
                topic=str(payload.get("topic", "")),
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="web_fetch",
            description="鎶撳彇缃戦〉姝ｆ枃骞舵彁鍙栧彲璇绘枃鏈€?",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                    "query": {"type": "string"},
                    "extract_depth": {"type": "string"},
                },
            },
            safety_policy="network-read",
            handler=lambda payload: client.fetch(
                url=str(payload.get("url", "")),
                max_chars=int(payload.get("max_chars", 1800)),
                query=str(payload.get("query", "")),
                extract_depth=str(payload.get("extract_depth", "")),
            ),
        )
    )
