from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from typing import Any

from app.config.settings import Settings


logger = logging.getLogger("clawfix")


class LLMClient:
    """OpenAI 兼容调用封装。

    优先使用官方 Python SDK；如果运行环境里没有安装 SDK，则回退到直接 HTTP 调用。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.enable_llm and bool(self.settings.llm_api_key)

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        model: str | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("LLM 未启用，请在 .env 中配置 OPENAI_API_KEY 并开启 ENABLE_LLM")

        errors: list[str] = []
        for route in self._completion_routes():
            try:
                response_text = self._create_structured_completion(
                    route=route,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema_name=schema_name,
                    schema=schema,
                    model=model,
                    temperature=temperature,
                )
                return self._parse_json_response(response_text)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{route}: {exc}")

        try:
            return self.complete_json_relaxed(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_name=schema_name,
                schema=schema,
                model=model,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

        raise RuntimeError("; ".join(errors))

    def complete_json_relaxed(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        model: str | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("LLM 未启用，请在 .env 中配置 OPENAI_API_KEY 并开启 ENABLE_LLM")

        relaxed_system_prompt = self._build_relaxed_system_prompt(system_prompt, schema_name, schema)
        errors: list[str] = []
        for route in self._completion_routes(prefer_chat=True):
            try:
                response_text = self._create_relaxed_completion(
                    route=route,
                    system_prompt=relaxed_system_prompt,
                    user_prompt=user_prompt,
                    model=model,
                    temperature=temperature,
                )
                return self._parse_json_response(response_text)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{route}-relaxed: {exc}")
        raise RuntimeError("; ".join(errors))

    def embed_texts(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        if not self.enabled:
            raise RuntimeError("Embedding 未启用，因为 LLM 未启用")
        if not texts:
            return []

        errors: list[str] = []
        for candidate_model in self._embedding_models(model):
            for attempt in range(2):
                try:
                    return self._embed_with_model(texts, candidate_model)
                except Exception as exc:  # noqa: BLE001
                    if attempt == 0 and self._should_retry_embedding_error(exc):
                        logger.warning("Embedding retry model=%s reason=%s", candidate_model, exc)
                        time.sleep(0.5)
                        continue
                    errors.append(f"{candidate_model}: {exc}")
                    break
        raise RuntimeError("; ".join(errors))

    def _responses_create(self, payload: dict[str, Any]) -> str:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return self._responses_create_via_http(payload)

        client = OpenAI(api_key=self.settings.llm_api_key, base_url=self.settings.llm_base_url)
        response = client.responses.create(**payload)
        output_text = getattr(response, "output_text", "") or ""
        if output_text:
            return output_text
        raw_dict = response.model_dump() if hasattr(response, "model_dump") else {}
        return self._extract_output_text(raw_dict)

    def _chat_completions_create(self, payload: dict[str, Any]) -> str:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return self._chat_completions_create_via_http(payload)

        client = OpenAI(api_key=self.settings.llm_api_key, base_url=self.settings.llm_base_url)
        response = client.chat.completions.create(**payload)
        message = response.choices[0].message if getattr(response, "choices", None) else None
        if message is None:
            raise RuntimeError(f"未能从 chat.completions 响应中提取消息: {response}")
        return self._extract_chat_message_text(getattr(message, "content", ""))

    def _responses_create_via_http(self, payload: dict[str, Any]) -> str:
        url = f"{self.settings.llm_base_url.rstrip('/')}/responses"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.settings.llm_api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            data = json.loads(response.read().decode(charset, errors="replace"))
        return self._extract_output_text(data)

    def _chat_completions_create_via_http(self, payload: dict[str, Any]) -> str:
        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.settings.llm_api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            data = json.loads(response.read().decode(charset, errors="replace"))
        return self._extract_chat_completion_text(data)

    def _embed_via_http(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        url = f"{self.settings.llm_base_url.rstrip('/')}/embeddings"
        payload = self._embedding_payload(texts, model=model)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.settings.llm_api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            data = json.loads(response.read().decode(charset, errors="replace"))
        return [list(item["embedding"]) for item in data.get("data", [])]

    def _embed_with_model(self, texts: list[str], model: str) -> list[list[float]]:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            return self._embed_via_http(texts, model=model)

        client = OpenAI(api_key=self.settings.llm_api_key, base_url=self.settings.llm_base_url)
        response = client.embeddings.create(**self._embedding_payload(texts, model=model))
        return [list(item.embedding) for item in response.data]

    def _embedding_models(self, preferred_model: str | None = None) -> list[str]:
        ordered: list[str] = []
        first = (preferred_model or self.settings.embedding_model).strip()
        if first and self._model_supports_dimensions(first):
            ordered.append(first)
        for item in self.settings.embedding_fallback_models:
            if item and item not in ordered and self._model_supports_dimensions(item):
                ordered.append(item)
        return ordered or ["text-embedding-3-small"]

    def _embedding_payload(self, texts: list[str], model: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model or self.settings.embedding_model, "input": texts}
        dimensions = self._embedding_dimensions(payload["model"])
        if dimensions is not None:
            payload["dimensions"] = dimensions
        return payload

    def _embedding_dimensions(self, model: str | None = None) -> int | None:
        dimensions = int(getattr(self.settings, "embedding_dimensions", 0) or 0)
        if dimensions <= 0:
            return None
        resolved_model = (model or self.settings.embedding_model or "").strip().lower()
        if resolved_model.startswith("text-embedding-3") and self._model_supports_dimensions(resolved_model):
            return dimensions
        return None

    def _model_supports_dimensions(self, model: str) -> bool:
        requested = int(getattr(self.settings, "embedding_dimensions", 0) or 0)
        if requested <= 0:
            return True
        resolved_model = (model or "").strip().lower()
        max_dims = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
        }.get(resolved_model)
        if max_dims is None:
            return True
        if requested <= max_dims:
            return True
        logger.warning(
            "Skipping incompatible embedding model=%s requested_dimensions=%s max_supported=%s",
            model,
            requested,
            max_dims,
        )
        return False

    def _should_retry_embedding_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return "timed out" in text or "timeout" in text or "connection reset" in text or "temporarily unavailable" in text

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str) and payload["output_text"]:
            return str(payload["output_text"])

        parts: list[str] = []
        for item in payload.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if not parts:
            raise RuntimeError(f"未能从模型响应中提取文本: {payload}")
        return "\n".join(parts)

    def _extract_chat_completion_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices", [])
        if not choices:
            raise RuntimeError(f"未能从 chat.completions 响应中提取 choices: {payload}")
        message = choices[0].get("message", {})
        return self._extract_chat_message_text(message.get("content", ""))

    def _extract_chat_message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return "\n".join(parts)
        raise RuntimeError(f"未能从 chat 消息中提取文本: {content}")

    def _create_structured_completion(
        self,
        *,
        route: str,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        model: str | None,
        temperature: float | None,
    ) -> str:
        if route == "responses":
            payload = {
                "model": model or self.settings.llm_model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
                "temperature": self.settings.llm_temperature if temperature is None else temperature,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
            }
            return self._responses_create(payload)

        payload = {
            "model": model or self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                },
            },
        }
        return self._chat_completions_create(payload)

    def _create_relaxed_completion(
        self,
        *,
        route: str,
        system_prompt: str,
        user_prompt: str,
        model: str | None,
        temperature: float | None,
    ) -> str:
        if route == "responses":
            payload = {
                "model": model or self.settings.llm_model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
                "temperature": self.settings.llm_temperature if temperature is None else temperature,
            }
            return self._responses_create(payload)

        payload = {
            "model": model or self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
        }
        return self._chat_completions_create(payload)

    def _completion_routes(self, prefer_chat: bool = False) -> list[str]:
        style = (self.settings.llm_api_style or "auto").lower()
        if style in {"chat", "chat_completions", "chat-completions"}:
            return ["chat_completions"]
        if style == "responses":
            return ["responses"]
        return ["chat_completions", "responses"] if prefer_chat else ["responses", "chat_completions"]

    def _parse_json_response(self, text: str) -> dict[str, Any]:
        candidates = [text.strip(), self._strip_markdown_fence(text), self._extract_json_block(text)]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
        raise RuntimeError(f"模型返回了非 JSON 结构化内容: {text[:800]}")

    def _strip_markdown_fence(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        return stripped.strip()

    def _extract_json_block(self, text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
        return ""

    def _build_relaxed_system_prompt(
        self,
        system_prompt: str,
        schema_name: str,
        schema: dict[str, object],
    ) -> str:
        return (
            system_prompt
            + "\n\n你必须只返回一个合法 JSON 对象，不要输出 Markdown、解释文字或代码块。"
            + f"\n目标 schema 名称：{schema_name}"
            + f"\n字段约束：{json.dumps(schema, ensure_ascii=False)}"
        )
