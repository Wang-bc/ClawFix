from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app.config.settings import Settings
from app.memory.search import MarkdownSearchEngine
from app.memory.store import KnowledgeStore
from app.sessions.session_store import SessionStore


class MarkdownSearchEngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        (self.workspace / "memory").mkdir(parents=True, exist_ok=True)
        (self.workspace / "memory" / "daily").mkdir(parents=True, exist_ok=True)
        (self.workspace / "cases").mkdir(parents=True, exist_ok=True)
        (self.workspace / "knowledge").mkdir(parents=True, exist_ok=True)
        (self.workspace / "MEMORY.md").write_text("# Stable Memory\nRedis incidents usually start from network or password checks.", encoding="utf-8")
        (self.workspace / "memory" / "runbook.md").write_text(
            "# Redis Runbook\n\nWhen connections are refused, check the port listener and firewall rules first.",
            encoding="utf-8",
        )
        (self.workspace / "memory" / "daily" / "2026-04-07.md").write_text(
            "# Daily Note\n\nOnlyInDaily should never appear in shared retrieval.",
            encoding="utf-8",
        )
        (self.workspace / "cases" / "redis_case.md").write_text(
            "# Redis Failure\n\nObserved timeout while connecting to Redis cluster.",
            encoding="utf-8",
        )
        self.store = SessionStore(self.workspace / ".sessions")
        self.store.append_message("coordinator", "session-1", "user", "Redis cluster keeps timing out")
        self.store.merge_durable_memory(
            "coordinator",
            "session-1",
            [
                {
                    "memory_id": "memory-1",
                    "kind": "fact",
                    "title": "Cluster timeout",
                    "content": "Redis cluster timeout appears after a firewall change.",
                    "checksum": "checksum-1",
                    "created_at": "2026-04-07T12:00:00",
                    "updated_at": "2026-04-07T12:00:00",
                }
            ],
        )
        self.store.append_message("coordinator", "session-2", "user", "MySQL auth failed")
        self.store.merge_durable_memory(
            "coordinator",
            "session-2",
            [
                {
                    "memory_id": "memory-2",
                    "kind": "fact",
                    "title": "MySQL auth",
                    "content": "Authentication failed after the password rotation.",
                    "checksum": "checksum-2",
                    "created_at": "2026-04-07T12:05:00",
                    "updated_at": "2026-04-07T12:05:00",
                }
            ],
        )
        self.engine = MarkdownSearchEngine(self.workspace)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_shared_search_returns_hits(self) -> None:
        hits = self.engine.search_shared("Redis connections refused", limit=5)
        self.assertTrue(hits)
        self.assertIn("Redis", hits[0].title)

    def test_shared_search_excludes_daily_memory(self) -> None:
        hits = self.engine.search_shared("OnlyInDaily", limit=5)
        self.assertFalse(hits)

    def test_session_memory_is_scoped(self) -> None:
        hits = self.engine.search_session_memory("session-1", "firewall timeout", limit=5)
        self.assertTrue(hits)
        self.assertEqual("Cluster timeout", hits[0].title)
        self.assertFalse(self.engine.search_session_memory("session-2", "firewall timeout", limit=5))

    def test_sync_knowledge_updates_shared_index(self) -> None:
        store = KnowledgeStore(self.workspace)
        self.engine.build_index(force=True)

        path = store.import_document(
            title="Sentinel quorum",
            content="Redis Sentinel quorum should stay above one in production.",
            relative_path="ops/sentinel.md",
        )
        sync_result = self.engine.sync_knowledge(path)
        self.assertFalse(sync_result["vector_written"])
        self.assertEqual("missing_settings", sync_result["disabled_reason"])

        hits = self.engine.search_shared("Sentinel quorum", limit=5)
        self.assertTrue(any(item.path == "knowledge/ops/sentinel.md" for item in hits))

        deleted_path = store.delete_document("knowledge/ops/sentinel.md")
        delete_result = self.engine.sync_knowledge(deleted_path)
        self.assertEqual("knowledge", delete_result["domain"])
        hits = self.engine.search_shared("Sentinel quorum", limit=5)
        self.assertFalse(any(item.path == "knowledge/ops/sentinel.md" for item in hits))

    def test_rebuild_index_does_not_duplicate_chunks(self) -> None:
        self.engine.build_index(force=True)
        initial_case_count = len(self.engine._case_chunks)  # type: ignore[attr-defined]
        initial_knowledge_count = len(self.engine._knowledge_chunks)  # type: ignore[attr-defined]

        self.engine.build_index(force=False)

        self.assertEqual(initial_case_count, len(self.engine._case_chunks))  # type: ignore[attr-defined]
        self.assertEqual(initial_knowledge_count, len(self.engine._knowledge_chunks))  # type: ignore[attr-defined]
        hits = self.engine.search_shared("Redis connections refused", limit=10)
        paths = [item.path for item in hits]
        self.assertEqual(len(paths), len(set(paths)))

    def test_vector_top_k_is_used_for_dense_route(self) -> None:
        settings = Settings(
            workspace_dir=self.workspace,
            enable_vector_search=True,
            vector_top_k=9,
        )
        engine = MarkdownSearchEngine(self.workspace, settings=settings)
        engine.build_index(force=True)
        engine._vector_search = Mock(return_value={})  # type: ignore[method-assign]

        engine.search_shared("Redis connections refused", limit=2)

        self.assertTrue(engine._vector_search.called)  # type: ignore[attr-defined]
        self.assertEqual(9, engine._vector_search.call_args.kwargs["limit"])  # type: ignore[attr-defined]

    def test_auto_qdrant_mode_prefers_local_outside_docker(self) -> None:
        settings = Settings(
            workspace_dir=self.workspace,
            enable_llm=True,
            llm_api_key="test-key",
            enable_vector_search=True,
            qdrant_mode="auto",
            qdrant_url="http://127.0.0.1:6333",
        )
        engine = MarkdownSearchEngine(self.workspace, settings=settings, llm_client=object())  # type: ignore[arg-type]
        self.assertEqual("local", engine._initial_qdrant_mode())  # type: ignore[attr-defined]

    def test_point_uuid_is_valid_uuid(self) -> None:
        point_id = self.engine._point_uuid("knowledge/runbook.md:1")  # type: ignore[attr-defined]
        self.assertEqual(point_id, str(uuid.UUID(point_id)))

    def test_query_error_does_not_disable_future_vector_writes(self) -> None:
        settings = Settings(
            workspace_dir=self.workspace,
            enable_llm=True,
            llm_api_key="test-key",
            enable_vector_search=True,
        )

        class EnabledClient:
            enabled = True

        engine = MarkdownSearchEngine(self.workspace, settings=settings, llm_client=EnabledClient())  # type: ignore[arg-type]
        engine._last_vector_query_error = "vector_query_failed: boom"  # type: ignore[attr-defined]

        self.assertTrue(engine._vector_enabled())  # type: ignore[attr-defined]

    def test_ensure_collection_ready_rebuilds_on_schema_mismatch(self) -> None:
        settings = Settings(
            workspace_dir=self.workspace,
            enable_llm=True,
            llm_api_key="test-key",
            enable_vector_search=True,
            embedding_dimensions=3072,
        )

        class EnabledClient:
            enabled = True

        engine = MarkdownSearchEngine(self.workspace, settings=settings, llm_client=EnabledClient())  # type: ignore[arg-type]
        engine._knowledge_chunks = [  # type: ignore[attr-defined]
            engine._document_chunks(self.workspace / "MEMORY.md", source_type="Knowledge")[0]
        ]
        fake_client = Mock()
        fake_client.collection_exists.return_value = True
        fake_client.get_collection.return_value = SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=1536)))
        )
        engine._upsert_chunks = Mock()  # type: ignore[method-assign]
        engine._recreate_collection = Mock()  # type: ignore[method-assign]

        rebuilt = engine._ensure_collection_ready(fake_client, engine._knowledge_collection_name)  # type: ignore[attr-defined]

        self.assertTrue(rebuilt)
        engine._recreate_collection.assert_called_once()
        engine._upsert_chunks.assert_called_once()

    def test_query_collection_supports_query_points_api(self) -> None:
        class QueryPointsOnlyClient:
            def __init__(self) -> None:
                self.calls = []

            def query_points(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(points=["ok"])

        fake_client = QueryPointsOnlyClient()

        result = self.engine._query_collection(  # type: ignore[attr-defined]
            fake_client,
            embedding=[0.1, 0.2],
            collection_name="demo",
            limit=3,
        )

        self.assertEqual(["ok"], result.points)
        self.assertEqual(1, len(fake_client.calls))


if __name__ == "__main__":
    unittest.main()
