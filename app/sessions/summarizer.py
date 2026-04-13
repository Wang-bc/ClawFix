from __future__ import annotations

import json

from app.config.models import SessionSummary
from app.llm.client import LLMClient
from app.llm.schemas import SESSION_SUMMARY_SCHEMA


class SessionSummarizer:
    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def summarize(
        self,
        previous_summary: dict[str, object] | None,
        messages: list[dict[str, object]],
    ) -> SessionSummary:
        previous_summary = previous_summary or {}
        transcript = "\n".join(
            f"[{item.get('role', 'unknown')}] {str(item.get('text', ''))[:800]}" for item in messages
        )

        if self.llm_client.enabled:
            system_prompt = (
                "你是会话压缩器。需要把历史对话压缩成稳定、面向后续排查的摘要。"
                "不要编造未出现的信息，保留关键环境、日志、已做动作、剩余疑问与可复用参考。"
            )
            user_prompt = (
                "请基于旧摘要和新增消息生成新的会话摘要。\n\n"
                f"旧摘要：\n{json.dumps(previous_summary, ensure_ascii=False)}\n\n"
                f"新增消息：\n{transcript}"
            )
            return SessionSummary(
                **self.llm_client.complete_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema_name="session_summary",
                    schema=SESSION_SUMMARY_SCHEMA,
                    model=self.llm_client.settings.llm_summary_model,
                    temperature=0.0,
                )
            )

        return SessionSummary(
            overview=" | ".join(str(item.get("text", ""))[:80] for item in messages[-4:]),
            known_facts=[str(item.get("text", ""))[:120] for item in messages if item.get("role") == "user"][-3:],
            attempted_actions=[
                str(item.get("text", ""))[:120]
                for item in messages
                if item.get("role") == "assistant"
            ][-3:],
            unresolved_questions=[],
            important_references=[],
        )
