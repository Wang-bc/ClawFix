from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.config.settings import Settings


class SettingsTestCase(unittest.TestCase):
    def test_relative_paths_resolve_from_project_root(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        original = {
            "ENV_FILE": os.environ.get("ENV_FILE"),
            "WORKSPACE_DIR": os.environ.get("WORKSPACE_DIR"),
            "PUBLIC_DIR": os.environ.get("PUBLIC_DIR"),
            "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL"),
            "LLM_PROVIDER": os.environ.get("LLM_PROVIDER"),
            "ENABLE_VECTOR_SEARCH": os.environ.get("ENABLE_VECTOR_SEARCH"),
            "QDRANT_URL": os.environ.get("QDRANT_URL"),
            "QDRANT_MODE": os.environ.get("QDRANT_MODE"),
            "EMBEDDING_MODEL": os.environ.get("EMBEDDING_MODEL"),
            "EMBEDDING_DIMENSIONS": os.environ.get("EMBEDDING_DIMENSIONS"),
            "OPENAI_EMBEDDING_MODEL": os.environ.get("OPENAI_EMBEDDING_MODEL"),
            "OPENAI_EMBEDDING_DIMENSIONS": os.environ.get("OPENAI_EMBEDDING_DIMENSIONS"),
            "WEB_SEARCH_PROVIDER": os.environ.get("WEB_SEARCH_PROVIDER"),
            "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY"),
            "TAVILY_BASE_URL": os.environ.get("TAVILY_BASE_URL"),
            "TAVILY_SEARCH_DEPTH": os.environ.get("TAVILY_SEARCH_DEPTH"),
            "TAVILY_EXTRACT_DEPTH": os.environ.get("TAVILY_EXTRACT_DEPTH"),
            "TAVILY_TOPIC": os.environ.get("TAVILY_TOPIC"),
        }
        try:
            os.environ.pop("WORKSPACE_DIR", None)
            os.environ.pop("PUBLIC_DIR", None)
            os.environ["ENABLE_VECTOR_SEARCH"] = "true"
            os.environ["ENV_FILE"] = ".env"
            os.environ["OPENAI_BASE_URL"] = "https://api.openai-proxy.org"
            os.environ["LLM_PROVIDER"] = "openai"
            os.environ["QDRANT_URL"] = "http://qdrant:6333"
            os.environ["QDRANT_MODE"] = "auto"
            os.environ["EMBEDDING_MODEL"] = "text-embedding-3-large"
            os.environ["EMBEDDING_DIMENSIONS"] = "3072"
            os.environ["OPENAI_EMBEDDING_MODEL"] = "text-embedding-3-large"
            os.environ["OPENAI_EMBEDDING_DIMENSIONS"] = "3072"
            os.environ["WEB_SEARCH_PROVIDER"] = "tavily"
            os.environ["TAVILY_API_KEY"] = "test-key"
            os.environ["TAVILY_BASE_URL"] = "https://api.tavily.test"
            os.environ["TAVILY_SEARCH_DEPTH"] = "advanced"
            os.environ["TAVILY_EXTRACT_DEPTH"] = "advanced"
            os.environ["TAVILY_TOPIC"] = "general"
            settings = Settings.from_env()
            self.assertEqual((project_root / "workspace").resolve(), settings.workspace_dir)
            self.assertEqual((project_root / "public").resolve(), settings.public_dir)
            self.assertEqual((project_root / "workspace" / "knowledge").resolve(), settings.knowledge_dir)
            self.assertEqual((project_root / "workspace" / ".index" / "retrieval.db").resolve(), settings.retrieval_db_path)
            self.assertEqual("https://api.openai-proxy.org/v1", settings.llm_base_url)
            self.assertTrue(settings.enable_vector_search)
            self.assertEqual("http://127.0.0.1:6333", settings.qdrant_url)
            self.assertEqual("auto", settings.qdrant_mode)
            self.assertEqual("text-embedding-3-large", settings.embedding_model)
            self.assertEqual(3072, settings.embedding_dimensions)
            self.assertEqual("tavily", settings.web_search_provider)
            self.assertEqual("test-key", settings.tavily_api_key)
            self.assertEqual("https://api.tavily.test", settings.tavily_base_url)
            self.assertEqual("advanced", settings.tavily_search_depth)
            self.assertEqual("advanced", settings.tavily_extract_depth)
            self.assertEqual("general", settings.tavily_topic)
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
