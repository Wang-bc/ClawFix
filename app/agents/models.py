from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AgentProfile:
    agent_id: str
    name: str
    description: str
    workspace_root: Path

