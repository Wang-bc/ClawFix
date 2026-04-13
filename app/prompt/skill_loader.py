from __future__ import annotations

from pathlib import Path


class SkillLoader:
    def __init__(self, workspace_root: Path) -> None:
        self.skills_dir = workspace_root / "skills"

    def load(self) -> list[str]:
        if not self.skills_dir.exists():
            return []
        return sorted(path.stem for path in self.skills_dir.glob("*.md"))

