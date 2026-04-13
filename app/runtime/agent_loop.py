from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable

from app.config.models import AgentRunRequest, DiagnosticResult, SessionMemoryItem
from app.memory.search import MarkdownSearchEngine
from app.memory.store import CaseStore
from app.prompt.builder import PromptBuilder
from app.runtime.diagnostic_engine import DiagnosticEngine
from app.runtime.events import build_event
from app.runtime.run_context import RunContext
from app.sessions.compactor import SessionCompactor
from app.sessions.context_guard import ContextGuard
from app.sessions.session_store import SessionStore


EventCallback = Callable[[dict[str, object]], None]


class AgentLoop:
    def __init__(
        self,
        session_store: SessionStore,
        context_guard: ContextGuard,
        compactor: SessionCompactor,
        prompt_builder: PromptBuilder,
        diagnostic_engine: DiagnosticEngine,
        case_store: CaseStore,
        search_engine: MarkdownSearchEngine,
    ) -> None:
        self.session_store = session_store
        self.context_guard = context_guard
        self.compactor = compactor
        self.prompt_builder = prompt_builder
        self.diagnostic_engine = diagnostic_engine
        self.case_store = case_store
        self.search_engine = search_engine

    def run(
        self,
        request: AgentRunRequest,
        event_callback: EventCallback | None = None,
    ) -> dict[str, object]:
        context = RunContext.from_request(request)
        events: list[dict[str, object]] = []

        def emit(stream: str, phase: str | None, payload: dict[str, object]) -> None:
            event = build_event(context.run_id, context.session_key, context.agent_id, stream, phase, payload)
            events.append(event)
            if event_callback:
                event_callback(event)

        emit("lifecycle", "start", {"source": request["source"], "deadline": context.deadline})
        self.session_store.append_message(
            agent_id=context.agent_id,
            session_key=context.session_key,
            role="user",
            text=request["user_text"],
            metadata={"source": request["source"]},
        )

        meta = self.session_store.get_session_meta(context.agent_id, context.session_key)
        recent_messages = self.session_store.load_messages(context.agent_id, context.session_key, limit=30)
        prepared_context = self.context_guard.prepare_context(recent_messages, meta.get("summary", {}))
        prompt_context = self.prompt_builder.build(
            context.agent_id,
            request["user_text"],
            prepared_context["messages"],
            session_key=context.session_key,
            session_summary=prepared_context["summary"],
            include_session_memory=True,
        )

        emit("assistant", "delta", {"message": "已完成会话上下文装配，开始生成诊断结果。"})
        result: DiagnosticResult = self.diagnostic_engine.analyze(request, prompt_context, emit)
        reply_text = result["reply_markdown"]

        for chunk in self._chunk_reply(reply_text):
            emit("assistant", "delta", {"chunk": chunk, "stream_role": "assistant"})

        self.session_store.append_message(
            agent_id=context.agent_id,
            session_key=context.session_key,
            role="assistant",
            text=reply_text,
            metadata={"diagnostic_result": result},
        )
        durable_memory = self.session_store.merge_durable_memory(
            context.agent_id,
            context.session_key,
            self._build_session_memory_items(result),
        )
        self.search_engine.sync_session_memory(context.session_key, durable_memory)
        self.case_store.record_analysis_note(context.session_key, request["user_text"], result)
        summary = self.compactor.maybe_compact(context.agent_id, context.session_key)
        emit("assistant", "complete", {"message": "本轮回复已完成。"})
        emit("lifecycle", "end", {"summary": result["summary"], "context_action": prepared_context["action"]})

        return {
            "run_id": context.run_id,
            "session_key": context.session_key,
            "agent_id": context.agent_id,
            "reply_text": reply_text,
            "result": result,
            "events": events,
            "prompt_context": prompt_context,
            "context_action": prepared_context["action"],
            "summary_snapshot": summary,
        }

    def _chunk_reply(self, text: str, chunk_size: int = 180) -> list[str]:
        if len(text) <= chunk_size:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            chunks.append(text[start:end])
            start = end
        return chunks

    def _build_session_memory_items(self, result: DiagnosticResult) -> list[SessionMemoryItem]:
        timestamp = datetime.now().isoformat(timespec="seconds")
        items: list[SessionMemoryItem] = []

        summary = str(result.get("summary", "")).strip()
        category = str(result.get("problem_category", "")).strip()
        if summary:
            items.append(
                self._make_memory_item(
                    kind="conclusion",
                    title=category or "Current conclusion",
                    content=summary,
                    timestamp=timestamp,
                )
            )

        for root_cause in result.get("candidate_root_causes", [])[:2]:
            title = str(root_cause.get("title", "")).strip()
            reasoning = str(root_cause.get("reasoning", "")).strip()
            if title and reasoning:
                items.append(
                    self._make_memory_item(
                        kind="fact",
                        title=title,
                        content=reasoning,
                        timestamp=timestamp,
                    )
                )

        for step in result.get("troubleshooting_steps", [])[:2]:
            step_text = str(step).strip()
            if not step_text:
                continue
            items.append(
                self._make_memory_item(
                    kind="action",
                    title="Troubleshooting step",
                    content=step_text,
                    timestamp=timestamp,
                )
            )

        deduped: dict[str, SessionMemoryItem] = {item["memory_id"]: item for item in items}
        return list(deduped.values())

    def _make_memory_item(self, *, kind: str, title: str, content: str, timestamp: str) -> SessionMemoryItem:
        normalized_title = " ".join(title.split())[:120]
        normalized_content = " ".join(content.split())[:480]
        checksum = hashlib.sha1(f"{kind}:{normalized_title}:{normalized_content}".encode("utf-8")).hexdigest()
        return SessionMemoryItem(
            memory_id=checksum[:20],
            kind=kind,
            title=normalized_title or "Memory item",
            content=normalized_content,
            checksum=checksum,
            created_at=timestamp,
            updated_at=timestamp,
        )
