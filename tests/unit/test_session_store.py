from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.sessions.session_store import SessionStore


class SessionStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.tempdir.name))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_append_and_list_sessions(self) -> None:
        self.store.append_message("coordinator", "session-1", "user", "first user message")
        self.store.append_message("coordinator", "session-1", "assistant", "first assistant reply")
        self.store.append_message("internal_retriever", "session-1::internal::run-1", "user", "child agent question")

        messages = self.store.load_messages("coordinator", "session-1")
        meta = self.store.get_session_meta("coordinator", "session-1")
        sessions = self.store.list_sessions()

        self.assertEqual(2, len(messages))
        self.assertEqual("session-1", meta["session_key"])
        self.assertEqual(2, meta["total_messages"])
        self.assertEqual(1, len(sessions))
        self.assertEqual("session-1", self.store.normalize_main_session_key("session-1::internal::run-1"))

    def test_merge_durable_memory_deduplicates_by_memory_id(self) -> None:
        self.store.append_message("coordinator", "session-2", "user", "redis timeout")
        merged = self.store.merge_durable_memory(
            "coordinator",
            "session-2",
            [
                {
                    "memory_id": "memory-1",
                    "kind": "fact",
                    "title": "Redis timeout",
                    "content": "Redis timeout appears after a firewall update.",
                    "checksum": "checksum-1",
                    "created_at": "2026-04-07T12:00:00",
                    "updated_at": "2026-04-07T12:00:00",
                }
            ],
        )
        merged = self.store.merge_durable_memory(
            "coordinator",
            "session-2",
            [
                {
                    "memory_id": "memory-1",
                    "kind": "fact",
                    "title": "Redis timeout",
                    "content": "Redis timeout appears after a firewall update.",
                    "checksum": "checksum-1",
                    "created_at": "2026-04-07T12:00:00",
                    "updated_at": "2026-04-07T12:05:00",
                }
            ],
        )

        self.assertEqual(1, len(merged))
        self.assertEqual("memory-1", merged[0]["memory_id"])
        self.assertEqual(1, len(self.store.get_durable_memory("coordinator", "session-2")))

    def test_delete_session_group_removes_main_and_child_sessions(self) -> None:
        self.store.append_message("coordinator", "session-3", "user", "main message")
        self.store.append_message("internal_retriever", "session-3::internal::run-1", "assistant", "child message")
        self.store.append_message("external_researcher", "session-3::external::run-2", "assistant", "child message")

        deleted = self.store.delete_session_group("session-3")

        self.assertEqual("session-3", deleted["session_key"])
        self.assertEqual(3, len(deleted["removed_session_keys"]))
        self.assertFalse(self.store.list_sessions(limit=10))


if __name__ == "__main__":
    unittest.main()
