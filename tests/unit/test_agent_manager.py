from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.agents.manager import AgentManager


class AgentManagerTestCase(unittest.TestCase):
    def test_evidence_judge_is_registered_as_formal_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            manager = AgentManager(workspace)

            agent_ids = [item.agent_id for item in manager.list_agents()]
            self.assertIn("evidence_judge", agent_ids)

            profile = manager.get_agent("evidence_judge")
            self.assertEqual(workspace / "agents" / "evidence_judge", profile.workspace_root)


if __name__ == "__main__":
    unittest.main()
