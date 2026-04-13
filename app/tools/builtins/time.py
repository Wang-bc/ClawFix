from __future__ import annotations

from datetime import datetime

from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolSpec


def register_time_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="time_now",
            description="返回当前本地时间。",
            input_schema={"type": "object", "properties": {}},
            safety_policy="read-only",
            handler=lambda payload: {"now": datetime.now().isoformat(timespec="seconds")},
        )
    )

