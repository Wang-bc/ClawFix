from __future__ import annotations

from typing import Any

from app.tools.registry import ToolRegistry


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def dispatch(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.registry.get(name).handler(payload)

