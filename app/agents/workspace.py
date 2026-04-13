from __future__ import annotations

from pathlib import Path


BOOTSTRAP_FILES = (
    "AGENTS.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "MEMORY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
)


class WorkspaceResolver:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def shared_bootstrap_paths(self) -> list[Path]:
        return [self.workspace_root / name for name in BOOTSTRAP_FILES]

    def agent_bootstrap_paths(self, agent_id: str) -> list[Path]:
        agent_root = self.workspace_root / "agents" / agent_id
        return [agent_root / name for name in BOOTSTRAP_FILES]

    def agent_root(self, agent_id: str) -> Path:
        return self.workspace_root / "agents" / agent_id
