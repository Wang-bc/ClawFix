from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.tools.builtins.web import WebToolClient


class _FakeHeaders:
    def get_content_charset(self) -> str:
        return "utf-8"

    def get_content_type(self) -> str:
        return "application/json"


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.headers = _FakeHeaders()

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class WebToolClientTestCase(unittest.TestCase):
    def test_tavily_search_uses_api_and_parses_results(self) -> None:
        requests: list[object] = []

        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            requests.append(request)
            return _FakeResponse(
                {
                    "results": [
                        {
                            "title": "Oracle Java docs",
                            "url": "https://docs.oracle.com/java",
                            "content": "NullPointerException documentation",
                            "score": 0.91,
                        }
                    ]
                }
            )

        client = WebToolClient(
            timeout_s=8,
            provider="tavily",
            tavily_api_key="test-key",
            tavily_base_url="https://api.tavily.test",
        )

        with patch("urllib.request.urlopen", fake_urlopen):
            result = client.search("NullPointerException", limit=2, search_depth="advanced", topic="general")

        self.assertEqual("tavily", result["provider"])
        self.assertEqual(1, len(result["results"]))
        self.assertEqual("Oracle Java docs", result["results"][0]["title"])
        self.assertEqual("https://api.tavily.test/search", requests[0].full_url)

    def test_tavily_extract_uses_query_aware_extract(self) -> None:
        requests: list[object] = []

        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            requests.append(request)
            return _FakeResponse(
                {
                    "results": [
                        {
                            "title": "Oracle Java docs",
                            "raw_content": "NullPointerException is thrown when an application attempts to use null.",
                        }
                    ]
                }
            )

        client = WebToolClient(
            timeout_s=8,
            provider="tavily",
            tavily_api_key="test-key",
            tavily_base_url="https://api.tavily.test",
        )

        with patch("urllib.request.urlopen", fake_urlopen):
            result = client.fetch(
                "https://docs.oracle.com/java",
                max_chars=120,
                query="NullPointerException",
                extract_depth="advanced",
            )

        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertTrue(result["ok"])
        self.assertEqual("https://api.tavily.test/extract", requests[0].full_url)
        self.assertEqual("NullPointerException", payload["query"])
        self.assertEqual(["https://docs.oracle.com/java"], payload["urls"])


if __name__ == "__main__":
    unittest.main()
