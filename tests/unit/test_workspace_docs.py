from __future__ import annotations

import unittest
from pathlib import Path


class WorkspaceDocsTestCase(unittest.TestCase):
    def test_agents_doc_mentions_all_formal_agents(self) -> None:
        workspace_agents = Path(__file__).resolve().parents[2] / "workspace" / "AGENTS.md"
        content = workspace_agents.read_text(encoding="utf-8")

        self.assertIn("4 agents", content)
        self.assertIn("coordinator", content)
        self.assertIn("internal_retriever", content)
        self.assertIn("external_researcher", content)
        self.assertIn("evidence_judge", content)


if __name__ == "__main__":
    unittest.main()
