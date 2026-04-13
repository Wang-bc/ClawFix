from __future__ import annotations

import unittest

from app.config.settings import Settings
from app.llm.client import LLMClient


class LLMClientEmbeddingPayloadTestCase(unittest.TestCase):
    def test_text_embedding_3_model_includes_dimensions(self) -> None:
        client = LLMClient(
            Settings(
                enable_llm=True,
                llm_api_key="test-key",
                embedding_model="text-embedding-3-large",
                embedding_dimensions=3072,
            )
        )

        payload = client._embedding_payload(["hello"], model="text-embedding-3-large")  # type: ignore[attr-defined]

        self.assertEqual(3072, payload["dimensions"])

    def test_non_openai_embedding_model_omits_dimensions(self) -> None:
        client = LLMClient(
            Settings(
                enable_llm=True,
                llm_api_key="test-key",
                embedding_model="custom-embedding-model",
                embedding_dimensions=3072,
            )
        )

        payload = client._embedding_payload(["hello"], model="custom-embedding-model")  # type: ignore[attr-defined]

        self.assertNotIn("dimensions", payload)

    def test_incompatible_fallback_embedding_model_is_skipped(self) -> None:
        client = LLMClient(
            Settings(
                enable_llm=True,
                llm_api_key="test-key",
                embedding_model="text-embedding-3-large",
                embedding_dimensions=3072,
                embedding_fallback_models=("text-embedding-3-small",),
            )
        )

        models = client._embedding_models()  # type: ignore[attr-defined]

        self.assertEqual(["text-embedding-3-large"], models)


if __name__ == "__main__":
    unittest.main()
