from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config.settings import Settings
from app.llm.client import LLMClient
from app.sessions.compactor import SessionCompactor
from app.sessions.context_guard import ContextGuard
from app.sessions.session_store import SessionStore
from app.sessions.summarizer import SessionSummarizer


class SessionCompactorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        settings = Settings(workspace_dir=root / "workspace", public_dir=root / "public")
        settings.ensure_directories()
        self.store = SessionStore(settings.sessions_dir)
        self.context_guard = ContextGuard(max_chars=2000, compact_threshold_chars=120)
        self.compactor = SessionCompactor(
            self.store,
            self.context_guard,
            SessionSummarizer(LLMClient(settings)),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_compaction_writes_structured_summary(self) -> None:
        for index in range(6):
            self.store.append_message("coordinator", "session-1", "user", f"这是第 {index} 次用户描述，包含较长的故障上下文。")
            self.store.append_message("coordinator", "session-1", "assistant", f"这是第 {index} 次助手回复，建议继续检查日志和配置。")

        summary = self.compactor.maybe_compact("coordinator", "session-1")
        meta = self.store.get_session_meta("coordinator", "session-1")

        self.assertIsInstance(summary, dict)
        self.assertTrue(summary["overview"])
        self.assertIn("summary", meta)
        self.assertIsInstance(meta["summary"], dict)


if __name__ == "__main__":
    unittest.main()
