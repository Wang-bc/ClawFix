from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config.settings import Settings
from app.gateway.server import AssistantApplication


class KnowledgeManagementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.settings = Settings(workspace_dir=root / "workspace", public_dir=root / "public")
        self.settings.ensure_directories()
        self.application = AssistantApplication(self.settings)

    def tearDown(self) -> None:
        self.application.shutdown()
        self.tempdir.cleanup()

    def test_handle_knowledge_import_list_and_delete(self) -> None:
        imported = self.application.handle_knowledge_import(
            {
                "title": "JVM GC notes",
                "path": "java/gc.md",
                "content": "GC logs help explain stop-the-world pauses.",
                "tags": ["java", "jvm"],
            }
        )

        self.assertEqual("knowledge/java/gc.md", imported["path"])
        self.assertIn("vector_sync", imported)

        listed = self.application.handle_knowledge(limit=10)["items"]
        self.assertEqual(1, len(listed))
        self.assertEqual("knowledge/java/gc.md", listed[0]["path"])

        hits = self.application.search_engine.search_shared("stop-the-world GC logs", limit=5)
        self.assertTrue(any(item.path == "knowledge/java/gc.md" for item in hits))

        deleted = self.application.handle_knowledge_delete({"path": "knowledge/java/gc.md"})
        self.assertEqual("knowledge/java/gc.md", deleted["path"])
        self.assertIn("vector_sync", deleted)
        self.assertFalse(self.application.handle_knowledge(limit=10)["items"])

    def test_handle_knowledge_import_with_filename_defaults_to_uploads_dir(self) -> None:
        imported = self.application.handle_knowledge_import(
            {
                "filename": "redis-notes.txt",
                "content": "Redis Sentinel needs quorum to elect a leader.",
            }
        )

        self.assertEqual("knowledge/uploads/redis-notes.txt", imported["path"])


if __name__ == "__main__":
    unittest.main()
