from __future__ import annotations

from app.memory.search import MarkdownSearchEngine
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolSpec


def register_memory_tools(registry: ToolRegistry, search_engine: MarkdownSearchEngine) -> None:
    registry.register(
        ToolSpec(
            name="memory_search",
            description="Search shared internal knowledge from cases and stable knowledge documents.",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}},
            safety_policy="read-only",
            handler=lambda payload: {
                "results": [
                    item.to_dict()
                    for item in search_engine.search_shared(
                        query=str(payload.get("query", "")),
                        limit=int(payload.get("limit", 5)),
                    )
                ]
            },
        )
    )
    registry.register(
        ToolSpec(
            name="memory_get",
            description="Read a workspace knowledge or case document by relative path.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            safety_policy="read-only",
            handler=lambda payload: search_engine.load_document(str(payload.get("path", ""))),
        )
    )
