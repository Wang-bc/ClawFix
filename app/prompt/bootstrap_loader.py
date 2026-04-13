from __future__ import annotations

from pathlib import Path

from app.agents.workspace import BOOTSTRAP_FILES, WorkspaceResolver


class BootstrapLoader:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.resolver = WorkspaceResolver(workspace_root)

    def load(self, agent_id: str) -> dict[str, str]:
        data: dict[str, str] = {}
        for path in self.resolver.shared_bootstrap_paths():
            if path.exists():
                data[f"shared/{path.name}"] = path.read_text(encoding="utf-8")

        for path in self.resolver.agent_bootstrap_paths(agent_id):
            if path.exists():
                data[f"agent/{path.name}"] = path.read_text(encoding="utf-8")

        # 若 agent 专属目录还未准备齐全，则回退到共享根目录下同名文件。
        if not any(key.startswith("agent/") for key in data):
            for filename in BOOTSTRAP_FILES:
                path = self.workspace_root / filename
                if path.exists():
                    data[f"agent/{filename}"] = path.read_text(encoding="utf-8")
        return data
