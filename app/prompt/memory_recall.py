from __future__ import annotations

from app.memory.search import MarkdownSearchEngine


class MemoryRecall:
    def __init__(self, search_engine: MarkdownSearchEngine) -> None:
        self.search_engine = search_engine

    def recall(self, session_key: str, query: str, limit: int = 3) -> list[dict[str, object]]:
        return [item.to_dict() for item in self.search_engine.search_session_memory(session_key, query, limit=limit)]
