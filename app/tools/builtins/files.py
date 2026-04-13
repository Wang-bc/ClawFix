from __future__ import annotations

from pathlib import Path

from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolSpec


def register_file_tools(registry: ToolRegistry, workspace_root: Path) -> None:
    def workspace_list(payload: dict[str, object]) -> dict[str, object]:
        relative = str(payload.get("path", "."))
        target = (workspace_root / relative).resolve()
        if not str(target).startswith(str(workspace_root.resolve())):
            raise ValueError("不允许访问工作区外部路径")
        return {"entries": [item.name for item in sorted(target.iterdir())]}

    def workspace_read(payload: dict[str, object]) -> dict[str, object]:
        relative = str(payload.get("path", ""))
        target = (workspace_root / relative).resolve()
        if not str(target).startswith(str(workspace_root.resolve())):
            raise ValueError("不允许访问工作区外部路径")
        return {"path": relative, "content": target.read_text(encoding="utf-8")}

    registry.register(
        ToolSpec(
            name="workspace_list",
            description="列出工作区目录内容。",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            safety_policy="read-only",
            handler=workspace_list,
        )
    )
    registry.register(
        ToolSpec(
            name="workspace_read",
            description="读取工作区文本文件。",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            safety_policy="read-only",
            handler=workspace_read,
        )
    )
